from datetime import date, timedelta

import httpx

from app.analysis import market_trend
from app.models import Bar
from app.public_market import PublicMarketProvider, WatchStrategy
from app.watch_history import apply_history, format_history, history_context


def data():
    end = date(2026, 10, 19)
    days = [end - timedelta(days=n) for n in reversed(range(1500))
            if (end - timedelta(days=n)).weekday() < 5]
    stock = [Bar(d, 100, 102, 99, 100, 1000) for d in days]
    index = [Bar(d, 10000, 10001, 9999, 10000, 1000) for d in days]
    return days, stock, index


def test_three_complete_disjoint_windows_and_point_in_time_market():
    days, stock, index = data()
    end = days[-1]
    peaks = []
    for offset, price in enumerate((200, 300, 400)):
        target = end - timedelta(weeks=52 * offset + 20)
        pos = max(i for i, d in enumerate(days) if d <= target)
        stock[pos] = Bar(days[pos], 100, price, 99, 100, 1000)
        peaks.append((days[pos], price))
    market = market_trend(index, end, days)
    result = history_context(stock, index, end, days, market)
    assert [row["high"] for row in result["periods"]] == [200, 300, 400]
    assert [row["day"] for row in result["periods"]] == [str(d) for d, _ in peaks]
    assert all(row["market"]["day"] == row["day"] for row in result["periods"])
    assert result["periods"][1]["end"] < result["periods"][0]["start"]
    future = end + timedelta(days=1)
    # A future stock/index spike must not alter historical highs or any comparison.
    changed = history_context(stock + [Bar(future, 999999, 999999, 999999, 999999, 1000)],
        index + [Bar(future, 1, 1, 1, 1, 1000)], end, days + [future], market)
    assert changed == result
    assert "400원" in format_history(result)


def test_history_boost_requires_price_strength_and_market_comparison():
    days, stock, index = data()
    for n, pos in enumerate(range(len(stock) - 21, len(stock))):
        price = 100 + n
        stock[pos] = Bar(days[pos], price, price + 2, price - 1, price, 1000)
    context = history_context(stock, index, days[-1], days, market_trend(index, days[-1], days))
    assert context["signal"] == 1
    assert context["current_relative"]["excess"] > 0
    values = {}
    assert apply_history(values, "관망", 1, WatchStrategy(), context) == ("매수", 2)
    assert apply_history({}, "의견 보류", 1, WatchStrategy(), context)[0] == "의견 보류"


def test_history_penalty_for_deep_drawdown_relative_weakness_and_worse_kospi():
    days, stock, index = data()
    for offset in range(3):
        target = days[-1] - timedelta(weeks=52 * offset + 20)
        pos = max(i for i, d in enumerate(days) if d <= target)
        stock[pos] = Bar(days[pos], 100, 200, 99, 100, 1000)
    for n, pos in enumerate(range(len(stock) - 21, len(stock))):
        price, idx = 100 - n * 2, 10000 - n * 100
        stock[pos] = Bar(days[pos], price, price + 2, price - 1, price, 1000)
        index[pos] = Bar(days[pos], idx, idx + 1, idx - 1, idx, 1000)
    result = history_context(stock, index, days[-1], days, market_trend(index, days[-1], days))
    assert result["signal"] == -1
    assert "코스피 악화" in result["reason"]


def test_missing_old_session_or_short_listing_disables_only_history_score():
    days, stock, index = data()
    end = days[-1]
    market = market_trend(index, end, days)
    gap = end - timedelta(weeks=130)
    missing = next(d for d in days if d >= gap)
    result = history_context([b for b in stock if b.day != missing], index, end, days, market)
    assert result["signal"] == 0
    assert "missing" in result["periods"][2]
    assert "비교 제외" in format_history(result)
    short = history_context(stock[-100:], index, end, days, market)
    assert short["signal"] == 0
    assert all("missing" in row for row in short["periods"])


def test_public_provider_requests_three_years_plus_indicator_warmup():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=b'<chart><item data="20261019|100|101|99|100|1000"/></chart>')

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = PublicMarketProvider(client)
        provider.daily_bars("005930", date(2026, 10, 19))
        provider.kospi_bars(date(2026, 10, 19))
    assert all(request.url.params["count"] == "1200" for request in requests)


def test_generation_snapshot_contains_history_and_keeps_withheld_out_of_hold(settings, store, monkeypatch):
    from app.briefings import BriefingEngine
    from app.briefing_views import daily_view
    from datetime import datetime, timezone

    days, stock, index = data()

    class Public:
        def daily_bars(self, symbol, end):
            return [b for b in stock if b.day <= end]

        def kospi_bars(self, end):
            return [b for b in index if b.day <= end]

        def close(self):
            pass

    monkeypatch.setattr("app.briefings.PublicMarketProvider", Public)
    store.put("watchlist", "005930", {"name": "삼성전자"})
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    body = BriefingEngine(settings, store).generate(now=now)
    assert "3년 고점 비교" in body
    proposal = store.get("watch_proposals", "005930")
    assert len(proposal["indicators"]["history_3y"]["periods"]) == 3
    view = daily_view(store, now.date())
    assert view["entries"][0]["action"] == proposal["action"]
    assert view["entries"][0]["text"] in body
