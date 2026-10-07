from dataclasses import replace
from datetime import date, timedelta

from app.briefings import BriefingEngine
from app.models import Bar


def test_generate_incremental_cache_and_immutable_recommendation(
    settings, configured, provider, fixed_now
):
    engine = BriefingEngine(settings, configured, provider)
    text = engine.generate(now=fixed_now)
    assert "기준 거래일: 2026-10-06" in text
    assert "의견: 매수" in text
    assert configured.briefing(fixed_now.date())[1] == text
    snapshot = configured.items("recommendations")
    count = len(provider.calls)
    assert engine.generate(now=fixed_now) == text
    assert len(provider.calls) == count
    assert configured.items("recommendations") == snapshot
    # New day: request only the missing trading day, not the initial 52 weeks.
    nxt = date(2026, 10, 7)
    provider.bars.append(Bar(nxt, 14500, 14600, 14400, 14500, 1500))
    engine.generate(now=fixed_now + timedelta(days=1))
    new_bars = [c for c in provider.calls[count:] if c[0] == "bars"]
    assert new_bars == [("bars", "005930", nxt, nxt)]


def test_missing_configuration_and_credentials_are_status_briefings(
    settings, store, provider, fixed_now
):
    text = BriefingEngine(settings, store, provider).generate(now=fixed_now)
    assert "데이터 확인 필요" in text and "전략 설정" in text
    assert not store.items("recommendations")
    text = BriefingEngine(settings, store).generate(now=fixed_now)
    assert "API 키가 없습니다" in text
    assert store.briefing(fixed_now.date())


def test_partial_failure_stale_holdings_and_flow_mismatch(
    settings, configured, provider, fixed_now
):
    configured.put("watchlist", "000660", {"name": "SK하이닉스"})
    configured.put(
        "manual_holdings", "000660", {"quantity": 0, "at": fixed_now.isoformat()}
    )
    provider.fail_symbols.add("000660")
    body = BriefingEngine(settings, configured, provider).generate(now=fixed_now)
    assert "시세 조회 실패" in body and "의견: 매수" in body
    configured.put(
        "manual_holdings",
        "005930",
        {"quantity": 0, "at": (fixed_now - timedelta(days=2)).isoformat()},
    )
    body = BriefingEngine(settings, configured, provider).generate(now=fixed_now)
    assert "보유량 기준 시각이 오래" in body
    assert "indicators" not in configured.get("proposals", "005930")


def test_holdings_only_selected_accounts_and_no_stale_reuse(
    settings, configured, provider, fixed_now
):
    configured.put(
        "accounts", "a", {"number": "12345678", "product": "01", "tracked": True}
    )
    configured.put(
        "accounts", "b", {"number": "87654321", "product": "01", "tracked": False}
    )
    engine = BriefingEngine(settings, configured, provider)
    assert "보유 3주" in engine.generate(now=fixed_now)
    assert [c for c in provider.calls if c[0] == "holdings"] == [
        ("holdings", "12345678")
    ]
    provider.fail_holdings = True
    assert "이전 잔고를 재사용하지 않습니다" in engine.generate(now=fixed_now)


def test_weekend_uses_last_trading_day(settings, configured, provider, fixed_now):
    # A source calendar must supply each closed day, including holidays.
    closed = fixed_now.date() - timedelta(days=1)
    original = provider.calendar
    provider.calendar = lambda start, end: original(start, end) | {closed: False}
    body = BriefingEngine(settings, configured, provider).generate(now=fixed_now)
    assert "기준 거래일: 2026-10-05" in body


def test_budget_is_shared_across_symbols(
    settings, configured, provider, fixed_now, strategy
):
    configured.put("settings", "strategy", replace(strategy, buy_budget=100_000).dict())
    configured.put("watchlist", "000660", {"name": "SK하이닉스"})
    configured.put(
        "manual_holdings", "000660", {"quantity": 0, "at": fixed_now.isoformat()}
    )
    BriefingEngine(settings, configured, provider).generate(now=fixed_now)
    recs = [v for _, v in configured.items("proposals")]
    assert (
        sum(
            v["quantity"]
            * v["indicators"]["close"]
            * (1 + (strategy.fee_bps + strategy.slippage_bps) / 10000)
            for v in recs
        )
        <= 100_000
    )
    assert recs[-1]["quantity"] == 0


def test_interior_gap_retried_without_refetching_complete_data(
    settings, configured, provider, fixed_now
):
    engine = BriefingEngine(settings, configured, provider)
    engine.generate(now=fixed_now)
    gap = provider.bars[-20].day
    with configured.connect() as db:
        db.execute(
            "DELETE FROM daily_bars WHERE symbol=? AND day=?",
            ("005930", gap.isoformat()),
        )
    provider.calls.clear()
    engine.generate(now=fixed_now)
    assert provider.calls == [("bars", "005930", gap, gap)]


def test_evaluation_freezes_original_price_and_cost_policy(
    settings, configured, provider, fixed_now, strategy
):
    engine = BriefingEngine(settings, configured, provider)
    engine.generate(now=fixed_now)
    key, rec = configured.items("recommendations")[0]
    after = []
    cursor = date.fromisoformat(rec["day"])
    while len(after) < strategy.evaluation_days:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            after.append(cursor)
    target = after[-1]
    configured.cache_market(
        "005930", bars=[Bar(target, 15000, 15100, 14900, 15000, 1000)]
    )
    engine.evaluate([date.fromisoformat(rec["day"])] + after, target)
    result = configured.get("evaluations", key)
    assert result["price"] == 15000 and result["day"] == target.isoformat()
    configured.cache_market(
        "005930", bars=[Bar(target, 16000, 16100, 15900, 16000, 1000)]
    )
    engine.evaluate(after, target)
    assert configured.get("evaluations", key) == result
