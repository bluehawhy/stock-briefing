from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from html import escape
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.config import Settings
from app.connections import digest, valid_registration
from app.models import DataError
from app.providers import KisProvider, Vault
from app.storage import Store

router = APIRouter()
HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def page(body: str) -> HTMLResponse:
    return HTMLResponse(
        '<!doctype html><html lang="ko"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>증권사 연결</title><body><h1>한국투자증권 조회 연결</h1>"
        + body
        + "</body></html>",
        headers=HEADERS,
    )


@router.get("/broker/register/{token}")
def form(token: str):
    settings = Settings.from_env()
    store = Store(settings.db_path)
    if not valid_registration(store, token):
        raise HTTPException(404)
    return page(
        "<p>5분 내 일회용 등록입니다. 실제 주문은 제출하지 않습니다.</p>"
        '<form method="post" autocomplete="off">'
        '<p><label>환경 <select name="environment"><option value="live">실전 조회</option>'
        '<option value="paper">모의 (일정·수급 미지원 시 분석 보류)</option></select></label></p>'
        '<p><label>App Key <input type="password" name="app_key" required maxlength="256"></label></p>'
        '<p><label>App Secret <input type="password" name="app_secret" required maxlength="512"></label></p>'
        '<p><label>계좌번호 앞 8자리 <input name="number" inputmode="numeric" pattern="[0-9]{8}" required></label></p>'
        '<p><label>상품코드 2자리 <input name="product" inputmode="numeric" pattern="[0-9]{2}" required></label></p>'
        "<p>등록 뒤 챗봇에서 추적 계좌를 직접 선택하세요. 추적하지 않는 계좌의 잔고는 조회하지 않습니다.</p>"
        '<button type="submit">키 검증·암호화 저장</button></form>'
    )


def save_connection(settings: Settings, store: Store, token: str, values: dict) -> str:
    # Keep credential replacement mutually exclusive with collection and other registrations.
    lease = store.acquire_lease("collection", 60)
    if not lease:
        raise DataError(
            "데이터 갱신 또는 연결 검증이 진행 중입니다. 잠시 후 다시 입력하세요."
        )
    try:
        if not valid_registration(store, token):
            raise DataError("등록 링크가 만료되었거나 이미 사용되었습니다.")
        environment, app_key, app_secret = (
            values.get("environment"),
            values.get("app_key", ""),
            values.get("app_secret", ""),
        )
        cano, product = values.get("number", ""), values.get("product", "")
        if (
            environment not in {"live", "paper"}
            or not 1 <= len(app_key) <= 256
            or not 1 <= len(app_secret) <= 512
            or len(cano) != 8
            or not cano.isascii()
            or not cano.isdigit()
            or len(product) != 2
            or not product.isascii()
            or not product.isdigit()
        ):
            raise DataError("입력 형식을 확인하세요.")
        credentials = {
            "environment": environment,
            "app_key": app_key,
            "app_secret": app_secret,
        }
        vault = Vault(settings)
        provider = KisProvider(settings, store, credentials=credentials)
        try:
            provider.authenticate()  # No balance request before the user's tracking selection.
        finally:
            provider.close()
        account_id = "KIS-" + digest(cano + ":" + product)[:10]
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            registration = db.execute(
                "SELECT value FROM records WHERE kind='registration' AND key=?",
                (digest(token),),
            ).fetchone()
            if not registration:
                raise DataError("등록 요청을 찾을 수 없습니다.")
            record = json.loads(registration[0])
            if (
                record["used"]
                or record["expires_at"] <= datetime.now(timezone.utc).isoformat()
            ):
                raise DataError("등록 링크가 만료되었거나 이미 사용되었습니다.")
            record["used"] = True
            Store.put_in(db, "registration", digest(token), record)
            Store.put_in(db, "credentials", "kis", vault.seal(credentials))
            # New keys may grant different accounts: require tracking reconfirmation.
            for key, row in db.execute(
                "SELECT key,value FROM records WHERE kind='accounts'"
            ).fetchall():
                account = json.loads(row)
                account["tracked"] = False
                Store.put_in(db, "accounts", key, account)
            Store.put_in(
                db,
                "accounts",
                account_id,
                {
                    "number": cano,
                    "product": product,
                    "tracked": False,
                    "provider": "kis",
                },
            )
            db.execute("DELETE FROM records WHERE kind='proposals'")
        store.audit("broker_registered", account_id)
        return account_id
    finally:
        store.release_lease("collection", lease)


@router.post("/broker/register/{token}")
async def submit(token: str, request: Request):
    settings = Settings.from_env()
    store = Store(settings.db_path)
    if not valid_registration(store, token):
        raise HTTPException(404)
    # A non-simple cross-origin request and browser-origin mismatch are rejected.
    from urllib.parse import urlparse

    expected = urlparse(settings.public_base_url)
    expected_origin = f"{expected.scheme}://{expected.netloc}"
    if request.headers.get("origin") != expected_origin:
        raise HTTPException(403)
    if (
        request.headers.get("content-type", "").split(";", 1)[0]
        != "application/x-www-form-urlencoded"
    ):
        raise HTTPException(415)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            raise HTTPException(413)
    try:
        parsed = parse_qs(body.decode(), max_num_fields=5, strict_parsing=True)
        if any(len(v) != 1 for v in parsed.values()):
            raise ValueError
        values = {k: v[0] for k, v in parsed.items()}
        account_id = await asyncio.to_thread(
            save_connection, settings, store, token, values
        )
    except DataError as exc:
        return page(
            "<p>" + escape(str(exc)) + "</p><p>챗봇에서 연결을 다시 요청하세요.</p>"
        )
    except (ValueError, UnicodeError):
        return page("<p>입력 형식을 확인하고 챗봇에서 연결을 다시 요청하세요.</p>")
    return page(
        "<p>API 키 검증·암호화 저장 완료. 계좌 권한은 첫 추적 조회에서 확인합니다.</p>"
        "<p>챗봇에서 <strong>추적 계좌 추가 "
        + escape(account_id)
        + "</strong>를 보내고 확인 코드를 입력하세요.</p>"
    )
