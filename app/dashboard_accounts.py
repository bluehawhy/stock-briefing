from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.config import Settings
from app.storage import Store

router = APIRouter()
HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def dashboard_registration_link(settings: Settings, store: Store, user_id: str) -> str:
    url = urlparse(settings.public_base_url)
    if url.scheme != "https" or not url.netloc or url.username or url.password or url.query or url.fragment:
        raise ValueError("PUBLIC_BASE_URL에 HTTPS 주소를 설정하세요.")
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    store.init()
    with store.connect() as db:
        db.execute(
            "INSERT INTO dashboard_registrations(token_hash,user_id,expires_at,used) VALUES (?,?,?,0)",
            (_token_hash(token), str(user_id), expires),
        )
    return settings.public_base_url + "/stock-briefing/register/" + token


def _valid(store: Store, token: str):
    if not 30 <= len(token) <= 100:
        return None
    store.init()
    with store.connect() as db:
        row = db.execute(
            "SELECT user_id,expires_at,used FROM dashboard_registrations WHERE token_hash=?",
            (_token_hash(token),),
        ).fetchone()
    if not row or row[2] or row[1] <= datetime.now(timezone.utc).isoformat():
        return None
    return str(row[0])


def _page(body: str) -> HTMLResponse:
    return HTMLResponse(
        '<!doctype html><html lang="ko"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>대시보드 계정 등록</title><body><h1>대시보드 계정 등록</h1>"
        + body + "</body></html>", headers=HEADERS,
    )


@router.get("/stock-briefing/register/{token}")
def registration_form(token: str):
    settings, store = Settings.from_env(), Store(Settings.from_env().db_path)
    if not _valid(store, token):
        raise HTTPException(404)
    return _page(
        "<p>이 링크는 5분 동안 한 번만 사용할 수 있습니다.</p>"
        f'<form method="post" action="/stock-briefing/register/{escape(token, quote=True)}" autocomplete="off">'
        '<label>아이디 <input name="username" minlength="3" maxlength="32" pattern="[A-Za-z0-9._-]+" required></label>'
        '<label>비밀번호 <input type="password" name="password" minlength="12" maxlength="1024" required></label>'
        '<label>비밀번호 확인 <input type="password" name="password_confirm" minlength="12" maxlength="1024" required></label>'
        '<button type="submit">등록</button></form>'
    )


@router.post("/stock-briefing/register/{token}")
async def register_account(token: str, request: Request):
    settings, store = Settings.from_env(), Store(Settings.from_env().db_path)
    user_id = _valid(store, token)
    if not user_id:
        raise HTTPException(404)
    expected = urlparse(settings.public_base_url)
    origin = f"{expected.scheme}://{expected.netloc}"
    if request.headers.get("origin") != origin:
        raise HTTPException(403)
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
        raise HTTPException(415)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 4096:
            raise HTTPException(413)
    try:
        fields = parse_qs(body.decode(), strict_parsing=True, max_num_fields=3)
        if any(len(v) != 1 for v in fields.values()):
            raise ValueError
        username = fields["username"][0].strip().lower()
        password = fields["password"][0]
        confirm = fields["password_confirm"][0]
    except (ValueError, KeyError, UnicodeError):
        return _page("<p>입력값을 확인하고 텔레그램에서 등록 링크를 다시 요청하세요.</p>")
    if not re.fullmatch(r"[a-z0-9._-]{3,32}", username) or not 12 <= len(password) <= 1024 or not hmac.compare_digest(password, confirm):
        return _page("<p>아이디 또는 비밀번호 조건을 확인하세요. 비밀번호는 12자 이상이어야 합니다.</p>")
    salt = secrets.token_bytes(16)
    encoded_salt = salt.hex()
    password_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000).hex()
    now = datetime.now(timezone.utc).isoformat()
    store.init()
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT user_id,expires_at,used FROM dashboard_registrations WHERE token_hash=?",
            (_token_hash(token),),
        ).fetchone()
        if not row or str(row[0]) != user_id or row[2] or row[1] <= now:
            raise HTTPException(404)
        existing = db.execute("SELECT username FROM dashboard_accounts WHERE user_id=?", (user_id,)).fetchone()
        if existing:
            db.execute(
                "UPDATE dashboard_accounts SET username=?,password_hash=?,password_salt=?,updated_at=? WHERE user_id=?",
                (username, password_hash, encoded_salt, now, user_id),
            )
        else:
            db.execute(
                "INSERT INTO dashboard_accounts(user_id,username,password_hash,password_salt,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                (user_id, username, password_hash, encoded_salt, now, now),
            )
        db.execute("UPDATE dashboard_registrations SET used=1 WHERE token_hash=?", (_token_hash(token),))
    return _page("<p>계정을 등록했습니다. 대시보드 로그인 화면에서 로그인하세요.</p><a href='/stock-briefing/login'>로그인</a>")
