from __future__ import annotations

from app.logs import traced, feature_event

from datetime import date, timedelta
from decimal import Decimal, ROUND_FLOOR
from statistics import mean

from app.models import Bar, DataError, Flow, Strategy


@traced("analysis.indicators")
def indicators(
    bars: list[Bar],
    flows: list[Flow],
    expected: date,
    s: Strategy,
    trading_days: list[date],
    *, include_flows: bool = True,
) -> dict:
    bars = sorted((b for b in bars if b.day <= expected), key=lambda b: b.day)
    flows_by_day = {f.day: f for f in flows}
    required = max(s.long_ma + 1, s.volume_window + 1, s.flow_window if include_flows else 0)
    if len(bars) < required or not bars or bars[-1].day != expected:
        raise DataError("일봉 부족 또는 기준 거래일 불일치")
    for bar in bars:
        bar.validate()
    year_start = expected - timedelta(days=364)
    required_days = [d for d in trading_days if year_start <= d <= expected]
    actual_days = {b.day for b in bars}
    if not required_days or not set(required_days).issubset(actual_days):
        raise DataError("52주 일봉 누락 또는 거래일 일정 부족")
    # Incomplete history for newly-listed stocks is explicitly withheld.
    if bars[0].day > min(required_days):
        raise DataError("52주 이력 부족")
    recent = bars[-s.flow_window :]
    if include_flows and any(b.day not in flows_by_day for b in bars[-s.flow_window - 1 :]):
        raise DataError("기관·외국인 수급 누락 또는 기준일 불일치")
    average_volume = mean(b.volume for b in bars[-s.volume_window - 1 : -1])
    if average_volume <= 0 or bars[-1].volume == 0:
        raise DataError("거래량 0: 거래정지 여부 확인 필요")
    year = [b for b in bars if b.day >= year_start]
    low, high = min(b.low for b in year), max(b.high for b in year)
    if high == low:
        raise DataError("52주 가격 범위가 0입니다.")
    short, long = (
        mean(b.close for b in bars[-s.short_ma :]),
        mean(b.close for b in bars[-s.long_ma :]),
    )
    previous_short = mean(b.close for b in bars[-s.short_ma - 1 : -1])
    previous_long = mean(b.close for b in bars[-s.long_ma - 1 : -1])
    values = {
        "close": bars[-1].close,
        "short_ma": short,
        "long_ma": long,
        "cross": "상향"
        if previous_short <= previous_long and short > long
        else "하향"
        if previous_short >= previous_long and short < long
        else "없음",
        "low_52w": low,
        "high_52w": high,
        "range_position": (bars[-1].close - low) / (high - low),
        "range_change": (bars[-1].close - bars[-2].close) / (high - low),
        "volume_ratio": bars[-1].volume / average_volume,
    }
    if include_flows:
        values.update({
            "institution": sum(flows_by_day[b.day].institution for b in recent),
            "foreign": sum(flows_by_day[b.day].foreign for b in recent),
            "institution_previous": sum(flows_by_day[b.day].institution for b in bars[-s.flow_window - 1 : -1]),
            "foreign_previous": sum(flows_by_day[b.day].foreign for b in bars[-s.flow_window - 1 : -1]),
        })
    feature_event("analysis.indicators", "ready", basis_day=expected, include_flows=include_flows, **values)
    return values


@traced("analysis.opinion")
def opinion(i: dict, s: Strategy, *, include_flows: bool = True) -> tuple[str, int]:
    trend = (
        1 if i["short_ma"] > i["long_ma"] else -1 if i["short_ma"] < i["long_ma"] else 0
    )
    signals = {
        "ma_trend": trend,
        "price_position": 1
        if i["close"] > i["long_ma"]
        else -1
        if i["close"] < i["long_ma"]
        else 0,
        "cross": 1 if i["cross"] == "상향" else -1 if i["cross"] == "하향" else 0,
        "range_position": 1
        if i["range_position"] <= s.range_buy_max
        else -1
        if i["range_position"] >= s.range_sell_min
        else 0,
        "volume": trend if i["volume_ratio"] >= s.volume_ratio else 0,
    }
    if include_flows:
        signals.update({
            "institution": (i["institution"] > 0) - (i["institution"] < 0),
            "foreign": (i["foreign"] > 0) - (i["foreign"] < 0),
        })
    score = sum(signal * s.weights[key] for key, signal in signals.items())
    feature_event("analysis.opinion", "decision", signals=signals, weights=s.weights,
                  score=score, buy_score=s.buy_score, sell_score=s.sell_score,
                  include_flows=include_flows)
    return (
        "매수" if score >= s.buy_score else "매도" if score <= s.sell_score else "관망",
        score,
    )


@traced("analysis.size")
def size(
    action: str, price: int, held: int, budget: int, s: Strategy
) -> tuple[int, str]:
    if price <= 0 or held < 0 or budget < 0:
        raise DataError("수량 계산 입력 오류")
    if action == "매수":
        # Include configured buy-side fee/slippage in the shared budget and position cap.
        unit = Decimal(price) * (
            1 + (Decimal(str(s.fee_bps)) + Decimal(str(s.slippage_bps))) / 10000
        )
        funds = min(budget, max(0, s.max_position - held * price))
        quantity = int((Decimal(funds) / unit).to_integral_value(rounding=ROUND_FLOOR))
        return (
            quantity,
            "예산·종목별 한도·비용 반영" if quantity else "예산 또는 종목별 한도 부족",
        )
    if action == "매도":
        quantity = int(
            (Decimal(held) * Decimal(str(s.sell_fraction))).to_integral_value(
                rounding=ROUND_FLOOR
            )
        )
        return (
            quantity,
            "보유량·매도 비율 반영" if quantity else "보유량 또는 매도 단위 부족",
        )
    return 0, "관망"


@traced("analysis.paper_result")
def paper_result(
    action: str, entry: int, exit_price: int, quantity: int, s: dict
) -> dict:
    # A sell opinion is evaluated as avoided loss on existing stock, never as a short sale.
    fee, tax, slip = (
        Decimal(str(s[k])) / 10000 for k in ("fee_bps", "tax_bps", "slippage_bps")
    )
    start, end = Decimal(entry), Decimal(exit_price)
    if action == "매수":
        pnl = (
            end * (1 - slip) * (1 - fee - tax) - start * (1 + slip) * (1 + fee)
        ) * quantity
    elif action == "매도":
        pnl = (
            start * (1 - slip) * (1 - fee - tax) - end * (1 + slip) * (1 + fee)
        ) * quantity
    else:
        raise DataError("평가 가능한 매매 의견이 아닙니다.")
    return {
        "pnl": float(pnl),
        "hit": pnl > 0,
        "definition": "매수 왕복 가상손익 / 매도 후 재매수 가정 회피손익 (공매도 아님)",
    }


@traced("analysis.kospi")
def market_trend(bars: list[Bar], expected: date, trading: list[date]) -> dict:
    """Observed market wave, not a fitted sine wave or a price forecast."""
    bars = sorted((b for b in bars if b.day <= expected), key=lambda b: b.day)
    if len(bars) < 63 or bars[-1].day != expected:
        raise DataError("코스피 일봉 부족 또는 기준 거래일 불일치")
    for bar in bars:
        bar.validate()
    days = [b.day for b in bars]
    if len(set(days)) != len(days) or not set(d for d in trading if days[-63] <= d <= expected).issubset(days):
        raise DataError("코스피 거래일 누락 또는 중복")
    fast = []
    for end in range(len(bars) - 5, len(bars) + 1):
        window = bars[end - 14:end]
        low, high = min(b.low for b in window), max(b.high for b in window)
        fast.append(50.0 if high == low else 100 * (window[-1].close - low) / (high - low))
    k, previous_k = mean(fast[-3:]), mean(fast[-4:-1])
    d = mean(mean(fast[n:n + 3]) for n in range(1, 4))
    ma20, ma60 = mean(b.close for b in bars[-20:]), mean(b.close for b in bars[-60:])
    previous20 = mean(b.close for b in bars[-21:-1])
    trend = 1 if bars[-1].close > ma20 > ma60 and ma20 > previous20 else -1 if bars[-1].close < ma20 < ma60 and ma20 < previous20 else 0
    wave = 1 if k > d and k > previous_k else -1 if k < d and k < previous_k else 0
    signal = max(-1, min(1, trend + wave))
    phase = "반등" if wave > 0 and trend <= 0 else "하락 전환" if wave < 0 and trend >= 0 else "상승" if signal > 0 else "하락" if signal < 0 else "횡보·혼조"
    return {"day": expected.isoformat(), "close": bars[-1].close / 100,
            "ma20": ma20 / 100, "ma60": ma60 / 100,
            "k": k, "d": d, "phase": phase, "signal": signal}


def apply_market(values: dict, action: str, score: int, strategy, market: dict) -> tuple[str, int]:
    values["market"] = market
    adjusted = score + market["signal"]
    result = "매수" if adjusted >= strategy.buy_score else "매도" if adjusted <= strategy.sell_score else "관망"
    if action == "의견 보류":
        result = action
    feature_event("analysis.market_adjustment", "decision", stock_score=score,
                  market=market, score=adjusted, action=result)
    return result, adjusted


def format_market(market: dict) -> str:
    return (f"• 코스피: {market['close']:,.2f} / {market['phase']} / 점수 {market['signal']:+d}\n"
            f"  20일선 {market['ma20']:,.2f} / 60일선 {market['ma60']:,.2f} / "
            f"스토캐스틱 K {market['k']:.1f}, D {market['d']:.1f}")
