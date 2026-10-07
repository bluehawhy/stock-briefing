from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings
from app.models import Bar, DataError, Flow, number, symbol_code
from app.storage import Store


class MarketProvider(Protocol):
    def calendar(self, start: date, end: date) -> dict[date, bool]: ...
    def daily_bars(self, symbol: str, start: date, end: date) -> list[Bar]: ...
    def investor_flows(self, symbol: str, start: date, end: date) -> list[Flow]: ...
    def holdings(self, account: dict) -> dict[str, int]: ...


class Vault:
    def __init__(self, settings: Settings):
        if not settings.master_key_file:
            raise DataError("MASTER_KEY_FILE 설정이 필요합니다.")
        try:
            path = Path(settings.master_key_file).resolve()
            if (
                path == settings.db_path.resolve()
                or settings.db_path.resolve().parent in path.parents
            ):
                raise ValueError
            if path.stat().st_mode & 0o077:
                raise ValueError
            self.cipher = Fernet(path.read_bytes().strip())
        except (OSError, ValueError):
            raise DataError(
                "암호화 키 파일은 DB 폴더 밖에 두고 권한을 600으로 제한하세요."
            ) from None

    def seal(self, value: dict) -> dict:
        return {"encrypted": self.cipher.encrypt(json.dumps(value).encode()).decode()}

    def open(self, value: dict) -> dict:
        try:
            return json.loads(self.cipher.decrypt(value["encrypted"].encode()))
        except (InvalidToken, KeyError, ValueError):
            raise DataError("저장된 인증정보를 복호화할 수 없습니다.") from None


class KisProvider:
    """Read-only personal KIS REST adapter. No order endpoint exists in this class."""

    def __init__(
        self,
        settings: Settings,
        store: Store,
        *,
        credentials: dict | None = None,
        client: httpx.Client | None = None,
    ):
        self.settings, self.store = settings, store
        if credentials is None:
            saved = store.get("credentials", "kis")
            credentials = (
                Vault(settings).open(saved)
                if saved
                else {
                    "app_key": settings.kis_app_key,
                    "app_secret": settings.kis_app_secret,
                    "environment": settings.kis_environment,
                }
            )
        if not credentials.get("app_key") or not credentials.get("app_secret"):
            raise DataError(
                "한국투자증권 API 키가 없습니다. '증권사 연결 한국투자증권'을 요청하세요."
            )
        self.credentials = credentials
        environment = credentials.get("environment", "live")
        if environment not in {"live", "paper"}:
            raise DataError("KIS_ENVIRONMENT는 live 또는 paper여야 합니다.")
        self.environment = environment
        self.base = (
            "https://openapi.koreainvestment.com:9443"
            if environment == "live"
            else "https://openapivts.koreainvestment.com:29443"
        )
        self.client = client or httpx.Client(timeout=15)
        self.owns_client = client is None
        self.token = ""
        self._last_request = 0.0

    def close(self):
        if self.owns_client:
            self.client.close()

    def _raw(self, method: str, path: str, **kwargs) -> tuple[dict, httpx.Headers]:
        for attempt in range(3):
            # Conservative pacing also applies to mock-investment endpoints.
            time.sleep(max(0, 0.55 - (time.monotonic() - self._last_request)))
            self._last_request = time.monotonic()
            try:
                response = self.client.request(method, self.base + path, **kwargs)
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        time.sleep(0.5 * (attempt + 1))
                        continue
                if response.status_code >= 400:
                    raise DataError(f"한국투자 API HTTP {response.status_code}")
                result = response.json()
                if not isinstance(result, dict):
                    raise ValueError
                if result.get("msg_cd") == "EGW00201" and attempt < 2:
                    time.sleep(attempt + 1)
                    continue
                if result.get("rt_cd", "0") != "0":
                    raise DataError("한국투자 API 조회 실패 (권한·토큰·호출한도 확인)")
                return result, response.headers
            except httpx.HTTPError:
                if attempt == 2:
                    raise DataError("한국투자 API 네트워크 오류") from None
            except (ValueError, TypeError):
                raise DataError("한국투자 API 응답 형식 오류") from None
        raise DataError("한국투자 API 재시도 한도 초과")

    def authenticate(self) -> None:
        lease = self.store.acquire_lease("kis-auth", 60)
        if not lease:
            raise DataError(
                "다른 작업이 토큰을 갱신 중입니다. 잠시 후 다시 시도하세요."
            )
        try:
            cache = self.store.get("credentials", "kis-token")
            if cache and self.settings.master_key_file:
                saved = Vault(self.settings).open(cache)
                if (
                    saved.get("app_key") == self.credentials["app_key"]
                    and saved.get("environment") == self.environment
                    and datetime.fromisoformat(saved["expires_at"])
                    > datetime.now(timezone.utc) + timedelta(minutes=5)
                ):
                    self.token = saved["token"]
                    self.store.put(
                        "provider_status",
                        "kis",
                        {"state": "connected", "expires_at": saved["expires_at"]},
                    )
                    return
            result, _ = self._raw(
                "POST",
                "/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self.credentials["app_key"],
                    "appsecret": self.credentials["app_secret"],
                },
            )
            self.token = result.get("access_token", "")
            lifetime = number(result.get("expires_in", 0))
            if not self.token or lifetime <= 0:
                raise DataError("한국투자 토큰 발급 실패")
            expiry = (
                datetime.now(timezone.utc) + timedelta(seconds=lifetime)
            ).isoformat()
            if self.settings.master_key_file:
                self.store.put(
                    "credentials",
                    "kis-token",
                    Vault(self.settings).seal(
                        {
                            "token": self.token,
                            "expires_at": expiry,
                            "app_key": self.credentials["app_key"],
                            "environment": self.environment,
                        }
                    ),
                )
            self.store.put(
                "provider_status", "kis", {"state": "connected", "expires_at": expiry}
            )
        except DataError:
            self.store.put("provider_status", "kis", {"state": "authentication_failed"})
            raise
        finally:
            self.store.release_lease("kis-auth", lease)

    def _get(self, path: str, tr: str, params: dict, continuation: str = ""):
        if not self.token:
            self.authenticate()
        return self._raw(
            "GET",
            path,
            params=params,
            headers={
                "authorization": "Bearer " + self.token,
                "appkey": self.credentials["app_key"],
                "appsecret": self.credentials["app_secret"],
                "tr_id": tr,
                "custtype": "P",
                "tr_cont": continuation,
            },
        )

    @staticmethod
    def _day(value: str) -> date:
        try:
            return datetime.strptime(value, "%Y%m%d").date()
        except (TypeError, ValueError):
            raise DataError("한국투자 거래일 형식 오류") from None

    def calendar(self, start: date, end: date) -> dict[date, bool]:
        if self.environment == "paper":
            raise DataError(
                "모의 API의 휴장일·수급 지원을 확인할 수 없습니다. 검증된 일정 데이터가 필요합니다."
            )
        days = {}
        cursor = start
        for _ in range(100):
            data, _ = self._get(
                "/uapi/domestic-stock/v1/quotations/chk-holiday",
                "CTCA0903R",
                {
                    "BASS_DT": cursor.strftime("%Y%m%d"),
                    "CTX_AREA_NK": "",
                    "CTX_AREA_FK": "",
                },
            )
            rows = data.get("output")
            if not isinstance(rows, list) or not rows:
                raise DataError("거래소 일정 조회 누락")
            for row in rows:
                day = self._day(row["bass_dt"])
                if row.get("opnd_yn") not in {"Y", "N"}:
                    raise DataError("거래소 개장 여부 누락")
                if start <= day <= end:
                    days[day] = row["opnd_yn"] == "Y"
            last = max(self._day(r["bass_dt"]) for r in rows)
            if last >= end:
                break
            if last < cursor:
                raise DataError("거래소 일정 조회 진행 실패")
            cursor = last + timedelta(days=1)
        if any(
            start + timedelta(days=n) not in days for n in range((end - start).days + 1)
        ):
            raise DataError("거래소 일정 일부 누락")
        return days

    def daily_bars(self, symbol: str, start: date, end: date) -> list[Bar]:
        symbol_code(symbol)
        bars = {}
        cursor = end
        for _ in range(30):
            data, _ = self._get(
                "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                "FHKST03010100",
                {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": symbol,
                    "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                    "FID_INPUT_DATE_2": cursor.strftime("%Y%m%d"),
                    "FID_PERIOD_DIV_CODE": "D",
                    "FID_ORG_ADJ_PRC": "0",
                },
            )
            rows = data.get("output2")
            if not isinstance(rows, list) or not rows:
                break
            parsed = []
            for row in rows:
                if not row.get("stck_bsop_date"):
                    continue
                bar = Bar(
                    self._day(row["stck_bsop_date"]),
                    *(
                        number(row[k])
                        for k in (
                            "stck_oprc",
                            "stck_hgpr",
                            "stck_lwpr",
                            "stck_clpr",
                            "acml_vol",
                        )
                    ),
                )
                bar.validate()
                parsed.append(bar)
                if start <= bar.day <= end:
                    bars[bar.day] = bar
            if not parsed:
                break
            earliest = min(b.day for b in parsed)
            if earliest <= start:
                break
            if earliest > cursor:
                raise DataError("일봉 페이지 진행 실패")
            cursor = earliest - timedelta(days=1)
            if cursor < start:
                break
        return sorted(bars.values(), key=lambda b: b.day)

    def investor_flows(self, symbol: str, start: date, end: date) -> list[Flow]:
        # This endpoint supplies recent finalized flows, not a synthetic 52-week history.
        data, _ = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            "FHKST01010900",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol_code(symbol)},
        )
        rows = data.get("output")
        if not isinstance(rows, list):
            raise DataError("투자자 수급 응답 누락")
        result = []
        for row in rows:
            day = self._day(row["stck_bsop_date"])
            if start <= day <= end:
                result.append(
                    Flow(
                        day,
                        number(row["orgn_ntby_qty"], negative=True),
                        number(row["frgn_ntby_qty"], negative=True),
                    )
                )
        return sorted(result, key=lambda f: f.day)

    def holdings(self, account: dict) -> dict[str, int]:
        cano, product = account["number"], account["product"]
        if (
            len(cano) != 8
            or not cano.isascii()
            or not cano.isdigit()
            or len(product) != 2
            or not product.isascii()
            or not product.isdigit()
        ):
            raise DataError("계좌번호 형식 오류")
        result, fk, nk, continuation = {}, "", "", ""
        seen = set()
        for _ in range(100):
            data, headers = self._get(
                "/uapi/domestic-stock/v1/trading/inquire-balance",
                "TTTC8434R" if self.environment == "live" else "VTTC8434R",
                {
                    "CANO": cano,
                    "ACNT_PRDT_CD": product,
                    "AFHR_FLPR_YN": "N",
                    "OFL_YN": "",
                    "INQR_DVSN": "02",
                    "UNPR_DVSN": "01",
                    "FUND_STTL_ICLD_YN": "N",
                    "FNCG_AMT_AUTO_RDPT_YN": "N",
                    "PRCS_DVSN": "00",
                    "CTX_AREA_FK100": fk,
                    "CTX_AREA_NK100": nk,
                },
                continuation,
            )
            rows = data.get("output1")
            if not isinstance(rows, list):
                raise DataError("보유량 응답 누락")
            for row in rows:
                qty = number(row["hldg_qty"])
                if qty:
                    result[symbol_code(row["pdno"])] = qty
            if headers.get("tr_cont") not in {"M", "F"}:
                return result
            fk, nk = data.get("ctx_area_fk100", ""), data.get("ctx_area_nk100", "")
            if not nk or (fk, nk) in seen:
                raise DataError("잔고 연속조회 실패: 부분 잔고 사용 금지")
            seen.add((fk, nk))
            continuation = "N"
        raise DataError("잔고 조회 페이지 한도 초과")


class MiraeAssetProvider:
    def __init__(self, *args, **kwargs):
        raise DataError(
            "미래에셋 국내주식 API 명세·개인 이용 권한 미확인: 연결 지원 준비 중입니다."
        )
