from __future__ import annotations

from app.logs import traced, feature_event

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.analysis import indicators, opinion, paper_result, size
from app.config import Settings
from app.models import DataError, Strategy
from app.providers import KisProvider
from app.public_market import PublicMarketProvider, watch_opinion, format_watch
from app.storage import Store

LOG = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


def _ranges(days: list[date]) -> list[tuple[date, date]]:
    """Group adjacent missing calendar days; never request a complete cached range."""
    result = []
    for day in sorted(days):
        if result and day == result[-1][1] + timedelta(days=1):
            result[-1] = (result[-1][0], day)
        else:
            result.append((day, day))
    return result


class BriefingEngine:
    def __init__(self, settings: Settings, store: Store, provider=None):
        self.settings, self.store, self.provider = settings, store, provider

    @traced("briefings.generate")
    def generate(self, *, now: datetime | None = None) -> str:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("An aware timestamp is required")
        day = now.astimezone(KST).date()
        lease = self.store.acquire_lease("collection", 7200)
        if not lease:
            raise DataError("데이터 갱신이 이미 진행 중입니다.")
        LOG.info("Briefing started: day=%s", day)
        self.collection_owner = lease
        owned_provider = self.provider is None
        provider = self.provider
        try:
            try:
                watch_body = self._generate_watchlist(day, now)
                has_holdings = any(a.get("tracked") for _, a in self.store.items("accounts")) or any(
                    h.get("quantity", 0) > 0 for _, h in self.store.items("manual_holdings")
                )
                holdings_body = "[보유종목]\n등록된 종목이 없습니다."
                if has_holdings:
                    try:
                        provider = provider or KisProvider(self.settings, self.store)
                        holdings_body = self._generate(day, now, provider)
                    except (DataError, KeyError, TypeError, ValueError) as exc:
                        reason = str(exc) if isinstance(exc, DataError) else "보유종목 데이터 오류"
                        LOG.warning("Holdings briefing withheld: day=%s reason=%s", day, reason)
                        holdings_body = f"[보유종목]\n데이터 확인 필요: {reason}\n의견·수량 보류"
                        for symbol, _ in self.store.items("proposals"):
                            self.store.put("proposals", symbol, {
                                "action": "데이터 확인 필요", "reason": reason,
                                "day": day.isoformat(), "at": now.isoformat(),
                            })
                body = f"발송일: {day}\n\n{holdings_body}\n\n{watch_body}"
                status = "generated"
            except (DataError, KeyError, TypeError, ValueError) as exc:
                # Never copy an arbitrary provider exception/response into chat or logs.
                reason = (
                    str(exc)
                    if isinstance(exc, DataError)
                    else "데이터 응답 필드 또는 설정 오류"
                )
                LOG.warning("Briefing withheld: day=%s reason=%s", day, reason)
                body = f"발송일: {day}\n데이터 확인 필요: {reason}\n매매 의견·수량을 보류합니다."
                status = "data_required"
                for symbol, _ in self.store.items("proposals"):
                    self.store.put(
                        "proposals",
                        symbol,
                        {
                            "action": "데이터 확인 필요",
                            "reason": reason,
                            "day": day.isoformat(),
                            "at": now.isoformat(),
                        },
                    )
            self.store.save_briefing(day, body)
            self.store.put(
                "runs", day.isoformat(), {"status": status, "at": now.isoformat()}
            )
            LOG.info("Briefing saved: day=%s status=%s", day, status)
            return body
        finally:
            if owned_provider and provider is not None:
                provider.close()
            self.store.release_lease("collection", lease)

    @traced("briefings._generate_watchlist")
    def _generate_watchlist(self, day: date, now: datetime) -> str:
        interests = dict(self.store.items("watchlist"))
        if not interests:
            return "[관심종목]\n등록된 종목이 없습니다."
        LOG.info("Watchlist analysis started: day=%s count=%d", day, len(interests))
        public = PublicMarketProvider()
        results = []
        try:
            try:
                strategy = Strategy.parse(self.store.get("settings", "strategy") or {})
                # Observed reference-stock sessions; never synthetic weekday calendars.
                end = day - timedelta(days=1)
                reference = public.daily_bars("005930", end)
                expected = reference[-1].day
                if (end - expected).days > 14:
                    raise DataError("공개 일봉 기준일이 오래되었습니다.")
                trading = [bar.day for bar in reference]
            except (DataError, KeyError, ValueError, TypeError) as exc:
                reason = str(exc) if isinstance(exc, DataError) else "공개 시장 데이터 오류"
                for symbol, info in sorted(interests.items()):
                    LOG.warning(
                        "Watchlist opinion withheld: day=%s symbol=%s stage=setup reason=%s",
                        day, symbol, reason,
                    )
                    results.append(f"{info.get('name', symbol)} ({symbol})\n의견 보류: {reason}")
                    self.store.put("watch_proposals", symbol, {
                        "action": "데이터 확인 필요", "reason": reason,
                        "at": now.isoformat(),
                    })
            else:
                for symbol, info in sorted(interests.items()):
                    if not self.store.renew_lease("collection", self.collection_owner):
                        raise DataError("수집 예약이 만료되었습니다. 다시 갱신하세요.")
                    try:
                        bars = reference if symbol == "005930" else public.daily_bars(symbol, end)
                        values, action, score = watch_opinion(bars, expected, strategy, trading)
                        self.store.put("watch_proposals", symbol, {
                            "symbol": symbol, "name": info.get("name", symbol),
                            "day": expected.isoformat(), "action": action,
                            "indicators": values, "score": score,
                            "strategy": strategy.dict(), "at": now.isoformat(),
                            "source": "naver_public", "basis": "price_volume_only",
                        })
                        LOG.info(
                            "Watchlist opinion ready: symbol=%s basis_day=%s action=%s score=%s",
                            symbol, expected, action, score,
                        )
                        results.append(format_watch(
                            info.get("name", symbol), symbol, expected, values, action, strategy
                        ))
                    except (DataError, KeyError, ValueError, TypeError) as exc:
                        reason = str(exc) if isinstance(exc, DataError) else "공개 일봉 데이터 오류"
                        LOG.warning(
                            "Watchlist opinion withheld: day=%s symbol=%s stage=analysis reason=%s",
                            day, symbol, reason,
                        )
                        results.append(f"{info.get('name', symbol)} ({symbol})\n의견 보류: {reason}")
                        self.store.put("watch_proposals", symbol, {
                            "action": "데이터 확인 필요", "reason": reason,
                            "at": now.isoformat(),
                        })
        finally:
            public.close()
        return (
            "[관심종목]\n공개 일봉 기준 · 가격/거래량 추세만 반영 (기관·외국인 수급 제외)\n\n"
            + "\n\n".join(results)
        )

    @traced("briefings._generate")
    def _generate(self, day: date, now: datetime, provider) -> str:
        yesterday = day - timedelta(days=1)
        start = yesterday - timedelta(days=400)
        # Store every date, including closed days. Weekday guesses cannot detect Korean holidays.
        calendar = {
            date.fromisoformat(k): v["open"] for k, v in self.store.items("calendar")
        }
        unknown = [
            start + timedelta(days=n)
            for n in range((yesterday - start).days + 1)
            if start + timedelta(days=n) not in calendar
        ]
        for first, last in _ranges(unknown):
            received = provider.calendar(first, last)
            for d, is_open in received.items():
                if first <= d <= last and isinstance(is_open, bool):
                    self.store.put("calendar", d.isoformat(), {"open": is_open})
                    calendar[d] = is_open
        if any(d not in calendar for d in unknown):
            raise DataError("거래소 일정 누락: 기준 거래일을 확정할 수 없습니다.")
        trading = sorted(
            d for d, is_open in calendar.items() if is_open and start <= d <= yesterday
        )
        if not trading:
            raise DataError("예상 거래일을 확정할 수 없습니다.")
        expected = trading[-1]
        strategy_values = self.store.get("settings", "strategy")
        strategy = Strategy.parse(strategy_values or {})
        tracked = [
            (key, a) for key, a in self.store.items("accounts") if a.get("tracked")
        ]
        holdings_error = None
        snapshots = []
        for key, account in tracked:
            try:
                holdings = provider.holdings(account)
                snapshot = {"quantities": holdings, "at": now.isoformat()}
                self.store.put("holdings", key, snapshot)
                snapshots.append(snapshot)
            except (DataError, KeyError, ValueError, TypeError):
                holdings_error = (
                    "추적 계좌 보유량 조회 실패: 이전 잔고를 재사용하지 않습니다."
                )
        interests = {}
        manual_holdings = dict(self.store.items("manual_holdings")) if not tracked else {}
        held_symbols = {
            symbol
            for snapshot in snapshots
            for symbol, quantity in snapshot["quantities"].items()
            if quantity > 0
        }
        held_symbols.update(
            symbol for symbol, holding in manual_holdings.items()
            if holding.get("quantity", 0) > 0
        )
        watch = dict(interests)
        for symbol in held_symbols:
            watch.setdefault(
                symbol, self.store.get("symbol_names", symbol, {"name": symbol})
            )
        if not watch:
            return f"기준 거래일: {expected}\n관심종목·추적 계좌 보유종목이 없습니다. '관심종목 추가 005930 삼성전자'로 등록하세요."
        budget = strategy.buy_budget
        results = {}
        complete = 0
        for symbol, info in sorted(watch.items()):
            if not self.store.renew_lease("collection", self.collection_owner):
                raise DataError(
                    "수집 예약이 만료되어 분석을 중단했습니다. 다시 갱신하세요."
                )
            try:
                self._collect(provider, symbol, trading, expected, strategy)
                bars, flows = self.store.bars(symbol), self.store.flows(symbol)
                values = indicators(bars, flows, expected, strategy, trading)
                if holdings_error:
                    raise DataError(holdings_error)
                if tracked:
                    held = sum(
                        snapshot["quantities"].get(symbol, 0) for snapshot in snapshots
                    )
                else:
                    manual = self.store.get("manual_holdings", symbol)
                    if manual is None:
                        raise DataError(
                            "보유량 미등록: 0주인 경우에도 '보유량 설정 종목코드 0'으로 확인하세요."
                        )
                    age = (
                        now - datetime.fromisoformat(manual["at"])
                    ).total_seconds() / 3600
                    if age < 0 or age > strategy.holdings_max_age_hours:
                        raise DataError(
                            "수동 보유량 기준 시각이 오래되었습니다. 다시 확인하세요."
                        )
                    held = manual["quantity"]
                action, score = opinion(values, strategy)
                quantity, reason = size(action, values["close"], held, budget, strategy)
                if action == "매수":
                    from decimal import Decimal, ROUND_CEILING

                    cost = Decimal(quantity * values["close"]) * (
                        1
                        + (
                            Decimal(str(strategy.fee_bps))
                            + Decimal(str(strategy.slippage_bps))
                        )
                        / 10000
                    )
                    budget -= int(cost.to_integral_value(rounding=ROUND_CEILING))
                result = {
                    "symbol": symbol,
                    "name": info.get("name", symbol),
                    "day": expected.isoformat(),
                    "action": action,
                    "quantity": quantity,
                    "held": held,
                    "indicators": values,
                    "score": score,
                    "reason": reason,
                    "strategy": strategy.dict(),
                    "at": now.isoformat(),
                }
                feature_event("holdings.opinion", "ready", symbol=symbol, basis_day=expected, action=action, score=score, quantity=quantity, reason=reason)
                self.store.put("proposals", symbol, result)
                # Preserve the first valid recommendation and its original cost/policy snapshot.
                key = day.isoformat() + ":" + symbol
                if (
                    action in {"매수", "매도"}
                    and quantity
                    and not self.store.get("recommendations", key)
                ):
                    self.store.put("recommendations", key, result)
                results[symbol] = self._format(result)
                complete += 1
            except (DataError, KeyError, ValueError, TypeError) as exc:
                reason = (
                    str(exc) if isinstance(exc, DataError) else "시장 데이터 필드 오류"
                )
                self.store.put(
                    "proposals",
                    symbol,
                    {
                        "action": "데이터 확인 필요",
                        "reason": reason,
                        "day": expected.isoformat(),
                        "at": now.isoformat(),
                    },
                )
                feature_event("holdings.opinion", "withheld", symbol=symbol, reason=reason)
                results[symbol] = (
                    f"{info.get('name', symbol)} ({symbol})\n데이터 확인 필요: {reason}\n의견·수량 보류"
                )
                self.store.put(
                    "collection",
                    symbol,
                    {
                        "state": "data_required",
                        "reason": reason,
                        "day": expected.isoformat(),
                        "at": now.isoformat(),
                    },
                )
        self.evaluate(trading, expected)
        self.store.put(
            "data_status",
            "latest",
            {
                "day": expected.isoformat(),
                "at": now.isoformat(),
                "complete": complete,
                "total": len(watch),
            },
        )
        if complete == len(watch):
            self.store.put(
                "data_status",
                "last_complete",
                {"day": expected.isoformat(), "at": now.isoformat()},
            )
        sections = []
        for title, symbols in (("보유종목", held_symbols),):
            entries = [results[symbol] for symbol in sorted(symbols)]
            section = f"[{title}]\n" + ("\n\n".join(entries) or "등록된 종목이 없습니다.")
            if title == "보유종목" and holdings_error:
                section += f"\n데이터 확인 필요: {holdings_error}"
            sections.append(section)
        return (
            f"기준 거래일: {expected} (전 거래일 종가, 주문 자동 제출 없음)\n"
            f"매수 예산: {strategy.buy_budget:,}원 / 제안 후 잔여: {budget:,}원\n"
            "종목코드 순서로 공통 매수 예산 배분 (중복 종목은 한 번만 계산)\n\n"
            + "\n\n".join(sections)
        )

    @traced("briefings._collect")
    def _collect(self, provider, symbol, trading, expected, strategy):
        feature_event("market.collection", "selected", symbol=symbol, basis_day=expected)
        bars = self.store.bars(symbol)
        cached = {b.day for b in bars}
        missing = [d for d in trading if d not in cached]
        if missing:
            # On bootstrap get a paged range; later fetch only missing intervals.
            ranges = [(missing[0], missing[-1])] if not bars else _ranges(missing)
            for first, last in ranges:
                received = provider.daily_bars(symbol, first, last)
                self.store.cache_market(
                    symbol, bars=[b for b in received if first <= b.day <= last]
                )
        flow_days = trading[-strategy.flow_window - 1 :]
        cached_flows = {f.day for f in self.store.flows(symbol)}
        missing_flows = [d for d in flow_days if d not in cached_flows]
        if missing_flows:
            flows = provider.investor_flows(symbol, missing_flows[0], expected)
            self.store.cache_market(
                symbol, flows=[f for f in flows if f.day in missing_flows]
            )
        self.store.put(
            "collection",
            symbol,
            {
                "state": "collected",
                "day": expected.isoformat(),
                "at": datetime.now(timezone.utc).isoformat(),
            },
        )

    @staticmethod
    def _format(r):
        i = r["indicators"]
        return (
            f"{r['name']} ({r['symbol']})\n의견: {r['action']} / 제안 {r['quantity']}주 / 보유 {r['held']}주\n"
            f"종가 {i['close']:,}원 / 점수 {r['score']} / {r['reason']}\n"
            f"이평 {i['short_ma']:,.1f}/{i['long_ma']:,.1f}, 교차 {i['cross']}\n"
            f"52주 {i['low_52w']:,}~{i['high_52w']:,}원, 위치 {i['range_position']:.1%}, 전일 변화 {i['range_change']:+.1%}\n"
            f"거래량 {i['volume_ratio']:.2f}배 / 기관 {i['institution']:+,}주 / 외국인 {i['foreign']:+,}주\n"
            f"직전 수급: 기관 {i['institution_previous']:+,}주 / 외국인 {i['foreign_previous']:+,}주"
        )

    @traced("briefings.evaluate")
    def evaluate(self, trading: list[date], expected: date) -> None:
        for key, rec in self.store.items("recommendations"):
            if self.store.get("evaluations", key):
                continue
            entry_day = date.fromisoformat(rec["day"])
            later = [d for d in trading if d > entry_day]
            horizon = rec["strategy"]["evaluation_days"]
            if len(later) < horizon:
                continue
            target = later[horizon - 1]
            bars = {b.day: b for b in self.store.bars(rec["symbol"])}
            # No interpolation, carry-forward, or evaluation on a missing trading day.
            if target > expected or target not in bars:
                continue
            result = paper_result(
                rec["action"],
                rec["indicators"]["close"],
                bars[target].close,
                rec["quantity"],
                rec["strategy"],
            )
            result.update(
                {
                    "day": target.isoformat(),
                    "price": bars[target].close,
                    "symbol": rec["symbol"],
                }
            )
            self.store.put("evaluations", key, result)


async def process_jobs(settings: Settings, store: Store) -> None:
    while True:
        job = store.claim_job()
        if job is None:
            await asyncio.sleep(1)
            continue
        try:
            await asyncio.to_thread(BriefingEngine(settings, store).generate)
            store.finish_job(
                job, "done", "브리핑 생성 완료. 브리핑 이력에서 저장된 결과를 확인하세요."
            )
        except Exception as exc:
            LOG.warning("Refresh job failed (%s)", type(exc).__name__, exc_info=True)
            store.finish_job(
                job, "failed", "갱신 실패. 서비스 상태 확인 후 다시 요청하세요."
            )
