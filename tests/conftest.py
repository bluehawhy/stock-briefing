from datetime import date, datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.models import Bar, Flow, Strategy
from app.storage import Store


@pytest.fixture
def settings(tmp_path):
    return Settings(
        tmp_path / "stock-briefing.db",
        "secret",
        frozenset({"k1"}),
        "fake-token",
        frozenset({"42"}),
    )


@pytest.fixture
def store(settings):
    result = Store(settings.db_path)
    result.init()
    return result


@pytest.fixture
def strategy():
    # Test policy only; these numbers are never installed as an operational strategy.
    return Strategy(
        5,
        20,
        5,
        5,
        1.2,
        0.2,
        0.9,
        2,
        -2,
        1_000_000,
        500_000,
        0.5,
        24,
        5,
        10,
        20,
        10,
        {
            "ma_trend": 1,
            "price_position": 1,
            "cross": 1,
            "range_position": 1,
            "volume": 1,
            "institution": 1,
            "foreign": 1,
        },
    )


@pytest.fixture
def fixed_now():
    return datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)


class FakeProvider:
    def __init__(self, end=date(2026, 10, 6)):
        self.calls = []
        self.fail_symbols = set()
        self.missing_flows = set()
        self.fail_holdings = False
        self.bars = []
        start = end - timedelta(days=440)
        for n in range(441):
            day = start + timedelta(days=n)
            if day.weekday() < 5:
                price = 10000 + n * 10
                self.bars.append(
                    Bar(day, price, price + 100, price - 100, price, 1000 + n)
                )

    def calendar(self, start, end):
        self.calls.append(("calendar", start, end))
        return {
            start + timedelta(days=n): (start + timedelta(days=n)).weekday() < 5
            for n in range((end - start).days + 1)
        }

    def daily_bars(self, symbol, start, end):
        from app.models import DataError

        self.calls.append(("bars", symbol, start, end))
        if symbol in self.fail_symbols:
            raise DataError("시세 조회 실패")
        return [b for b in self.bars if start <= b.day <= end]

    def investor_flows(self, symbol, start, end):
        self.calls.append(("flows", symbol, start, end))
        return [
            Flow(b.day, 100, 200)
            for b in self.bars
            if start <= b.day <= end and b.day not in self.missing_flows
        ]

    def holdings(self, account):
        from app.models import DataError

        self.calls.append(("holdings", account["number"]))
        if self.fail_holdings:
            raise DataError("잔고 조회 실패")
        return {"005930": 3}

    def close(self):
        pass


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture
def configured(store, strategy, fixed_now):
    store.put("settings", "strategy", strategy.dict())
    store.put("watchlist", "005930", {"name": "삼성전자"})
    store.put("manual_holdings", "005930", {"quantity": 0, "at": fixed_now.isoformat()})
    return store
