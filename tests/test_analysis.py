from dataclasses import replace
from datetime import date

import pytest

from app.analysis import indicators, opinion, paper_result, size
from app.models import Bar, DataError, Flow, Strategy, number, symbol_code


def test_indicators_and_explained_score(provider, strategy):
    expected = date(2026, 10, 6)
    bars = provider.bars
    flows = [Flow(b.day, 10, -20) for b in bars]
    values = indicators(bars, flows, expected, strategy, [b.day for b in bars])
    assert values["institution"] == 50
    assert values["foreign"] == -100
    assert values["high_52w"] > values["close"] > values["low_52w"]
    assert values["short_ma"] > values["long_ma"]
    assert 0 < values["range_position"] < 1
    assert opinion(values, strategy)[0] in {"매수", "관망", "매도"}


def test_golden_and_death_cross(provider, strategy):
    original = provider.bars
    for final, direction in [(11000, "상향"), (9000, "하향")]:
        bars = [Bar(b.day, 10000, 10050, 9950, 10000, 1000) for b in original[:-1]]
        bars.append(Bar(original[-1].day, final, final + 50, final - 50, final, 1000))
        values = indicators(
            bars,
            [Flow(b.day, 0, 0) for b in bars],
            bars[-1].day,
            strategy,
            [b.day for b in bars],
        )
        assert values["cross"] == direction


def test_history_gap_and_flow_mismatch_fail_closed(provider, strategy):
    bars = provider.bars
    flows = [Flow(b.day, 1, 1) for b in bars]
    trading = [b.day for b in bars]
    with pytest.raises(DataError, match="52주"):
        indicators(bars[:-20] + bars[-19:], flows, bars[-1].day, strategy, trading)
    with pytest.raises(DataError, match="수급"):
        indicators(bars, flows[:-1], bars[-1].day, strategy, trading)
    with pytest.raises(DataError, match="기준 거래일"):
        indicators(bars[:-1], flows, bars[-1].day, strategy, trading)


def test_buy_budget_includes_costs_and_held_position(strategy):
    s = replace(strategy, max_position=1000, buy_budget=1000)
    assert size("매수", 100, 0, 1000, s)[0] == 9
    assert size("매수", 100, 8, 1000, s)[0] == 1
    assert size("매수", 100, 10, 1000, s)[0] == 0
    assert size("매수", 100, 0, 0, s)[0] == 0
    assert size("매도", 100, 3, 1000, s)[0] == 1
    assert size("매도", 100, 0, 1000, s)[0] == 0


@pytest.mark.parametrize("value", ["nan", "inf", "1.1", "-1", True, "bad"])
def test_invalid_numbers(value):
    with pytest.raises(DataError):
        number(value)


def test_strategy_requires_explicit_complete_configuration(strategy):
    with pytest.raises(DataError):
        Strategy.parse({})
    for changes in [
        {"short_ma": 20},
        {"fee_bps": float("nan")},
        {"range_buy_max": 1},
        {"buy_budget": -1},
        {"flow_window": 0},
        {"long_ma": 20.5},
        {"sell_fraction": 0},
    ]:
        with pytest.raises(DataError):
            Strategy.parse(strategy.dict() | changes)
    assert Strategy.parse(strategy.dict()) == strategy
    with pytest.raises(DataError):
        symbol_code("００５９３０")


def test_paper_costs_and_sell_avoided_loss(strategy):
    buy = paper_result("매수", 100, 110, 2, strategy.dict())
    sell = paper_result("매도", 110, 100, 2, strategy.dict())
    assert 0 < buy["pnl"] < 20
    assert 0 < sell["pnl"] < 20
    assert not paper_result("매수", 100, 100, 1, strategy.dict())["hit"]


def test_explicit_weights_drive_decision(provider, strategy):
    bars = provider.bars
    values = indicators(
        bars,
        [Flow(b.day, 10, -20) for b in bars],
        bars[-1].day,
        strategy,
        [b.day for b in bars],
    )
    weights = {k: 0 for k in strategy.weights}
    weights["foreign"] = 3
    s = Strategy.parse(strategy.dict() | {"weights": weights})
    assert opinion(values, s) == ("매도", -3)
    with pytest.raises(DataError):
        Strategy.parse(strategy.dict() | {"weights": {k: 0 for k in weights}})
