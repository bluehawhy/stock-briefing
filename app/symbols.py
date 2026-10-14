"""Resolve stock names using the official public master and optional KIS lookup."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from zipfile import ZipFile, BadZipFile

import httpx

from app.logs import traced, feature_event
from app.models import DataError, symbol_code
from app.providers import KisProvider


def normalized_name(name: str) -> str:
    return "".join(name.split()).casefold()


class SymbolResolver:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store

    @traced("symbols.public_catalog")
    def catalog(self) -> dict[str, dict]:
        now = datetime.now(timezone.utc)
        cached = self.store.get("symbol_catalog", "domestic")
        if cached and cached.get("expires_at", "") > now.isoformat():
            return cached["symbols"]
        symbols = {}
        try:
            with httpx.Client(timeout=8, follow_redirects=True) as client:
                for market, tail in (("kospi", 228), ("kosdaq", 222)):
                    filename = f"{market}_code.mst"
                    response = client.get(
                        f"https://new.real.download.dws.co.kr/common/master/{filename}.zip"
                    )
                    response.raise_for_status()
                    if len(response.content) > 5_000_000:
                        raise DataError("공개 종목 목록 크기 초과")
                    with ZipFile(BytesIO(response.content)) as archive:
                        info = archive.getinfo(filename)
                        if info.file_size > 15_000_000:
                            raise DataError("공개 종목 목록 압축 해제 크기 초과")
                        # Official format counts the trailing newline in the fixed suffix.
                        rows = archive.read(info).decode("cp949").splitlines(keepends=True)
                    market_symbols = {}
                    for row in rows:
                        prefix = row[:-tail]
                        code, name = prefix[:9].strip(), prefix[21:].strip()
                        # Existing bot supports six-digit domestic stock codes only.
                        if len(code) == 6 and code.isascii() and code.isdigit() and name:
                            market_symbols[code] = {"name": name, "market": market}
                    if not market_symbols:
                        raise DataError("공개 종목 목록 형식 오류")
                    symbols.update(market_symbols)
        except (httpx.HTTPError, BadZipFile, KeyError, UnicodeError, ValueError):
            feature_event("symbols.public_catalog", "unavailable")
            raise DataError("공개 종목 목록 조회 실패: 잠시 후 다시 시도하거나 6자리 코드를 보내세요.") from None
        self.store.put("symbol_catalog", "domestic", {
            "symbols": symbols, "updated_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=24)).isoformat(),
        })
        feature_event("symbols.public_catalog", "ready", count=len(symbols))
        return symbols

    @traced("symbols.resolve")
    def resolve(self, name: str) -> list[str]:
        query = normalized_name(name)
        known = dict(self.store.items("symbol_names"))
        known.update(dict(self.store.items("watchlist")))
        candidates = [code for code, info in known.items()
                      if normalized_name(info["name"]) == query]
        has_api = bool(self.store.get("credentials", "kis") or (
            self.settings.kis_app_key and self.settings.kis_app_secret
        ))
        # KIS takes a stock code, not a company name. Known codes can be checked
        # directly; new names need a public-master candidate before API validation.
        provider = None
        if has_api:
            try:
                provider = KisProvider(self.settings, self.store)
                for code in candidates if len(candidates) == 1 else []:
                    info = provider.stock_info(code)
                    if normalized_name(info["name"]) == query:
                        feature_event("symbols.resolve", "ready", source="kis_api", symbol=code)
                        return [code]
            except (DataError, KeyError, ValueError, TypeError):
                feature_event("symbols.resolve", "api_fallback")
                if provider:
                    provider.close()
                provider = None
        try:
            catalog = self.catalog()
            matches = [code for code, info in catalog.items()
                       if normalized_name(info["name"]) == query]
            if len(matches) == 1:
                code = symbol_code(matches[0])
                if provider:
                    try:
                        info = provider.stock_info(code)
                    except (DataError, KeyError, ValueError, TypeError):
                        feature_event("symbols.resolve", "api_fallback", symbol=code)
                    else:
                        if normalized_name(info["name"]) != query:
                            raise DataError("API 종목명과 공개 목록이 다릅니다. 6자리 코드를 확인하세요.")
                        feature_event("symbols.resolve", "ready", source="kis_api", symbol=code)
                        return matches
                feature_event("symbols.resolve", "ready", source="public_master", symbol=code)
            return sorted(matches)
        finally:
            if provider:
                provider.close()
