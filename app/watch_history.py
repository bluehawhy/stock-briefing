"""Three non-overlapping 52-week highs and point-in-time KOSPI comparisons."""
from datetime import date, timedelta
from statistics import median

from app.analysis import market_trend
from app.models import DataError


def relative_20(stock, index, day, trading):
    sessions = [d for d in trading if d <= day][-21:]
    if len(sessions) != 21:
        raise DataError("20거래일 비교 이력 부족")
    stocks = {b.day: b for b in stock if b.day <= day}
    indices = {b.day: b for b in index if b.day <= day}
    if any(d not in stocks or d not in indices for d in sessions):
        raise DataError("20거래일 비교 데이터 누락")
    first, last = sessions[0], sessions[-1]
    stock_return = stocks[last].close / stocks[first].close - 1
    index_return = indices[last].close / indices[first].close - 1
    return {"stock_return": stock_return, "index_return": index_return,
            "excess": stock_return - index_return}


def history_context(stock, index, expected: date, trading, current_market):
    stock = sorted((b for b in stock if b.day <= expected), key=lambda b: b.day)
    index = sorted((b for b in index if b.day <= expected), key=lambda b: b.day)
    observed = {b.day for b in stock}
    periods = []
    result = {"periods": periods, "signal": 0, "reason": "3년 비교 이력 부족·판단 제외"}
    for offset in range(3):
        end = expected - timedelta(weeks=52 * offset)
        boundary = end - timedelta(weeks=52)
        start = boundary + timedelta(days=1)
        row = {"start": start.isoformat(), "end": end.isoformat()}
        periods.append(row)
        sessions = [d for d in trading if start <= d <= end]
        if (not sessions or not trading or trading[0] > boundary
                or not stock or stock[0].day > sessions[0]
                or not set(sessions).issubset(observed)):
            row["missing"] = "해당 52주 전체 이력 부족 또는 거래일 누락"
            continue
        bars = [b for b in stock if start <= b.day <= end]
        # Most recent date on ties; no future prices/index observations at the peak date.
        peak = max(bars, key=lambda b: (b.high, b.day))
        row.update(high=peak.high, day=peak.day.isoformat(),
                   distance=stock[-1].close / peak.high - 1)
        try:
            row["market"] = market_trend(index, peak.day, trading)
            row["relative"] = relative_20(stock, index, peak.day, trading)
            row["market_signal_change"] = current_market["signal"] - row["market"]["signal"]
        except DataError as exc:
            row["missing"] = str(exc)
    try:
        current = relative_20(stock, index, expected, trading)
        result["current_relative"] = current
    except DataError as exc:
        result["reason"] = str(exc) + "·판단 제외"
        return result
    if any("missing" in row for row in periods):
        return result
    historical_excess = median(row["relative"]["excess"] for row in periods)
    historical_market = median(row["market"]["signal"] for row in periods)
    high = max(row["high"] for row in periods)
    close = stock[-1].close
    signal = 0
    reason = "고점 가격·당시 코스피 대비 뚜렷한 추가 신호 없음"
    if (close >= high * 0.95 and current["excess"] > max(0, historical_excess)
            and current_market["signal"] >= max(0, historical_market)):
        signal, reason = 1, "3년 최고가 5% 이내·과거 고점 당시보다 시장 대비 강세·코스피 동조"
    elif (close <= high * 0.85 and current["excess"] < min(0, historical_excess)
          and current_market["signal"] < min(0, historical_market)):
        signal, reason = -1, "3년 최고가 대비 15% 이상 하락·과거보다 시장 대비 약세·코스피 악화"
    result.update(signal=signal, reason=reason, historical_excess_median=historical_excess,
                  historical_market_median=historical_market)
    return result


def apply_history(values, action, score, strategy, context):
    values["history_3y"] = context
    score += context["signal"]
    result = "매수" if score >= strategy.buy_score else "매도" if score <= strategy.sell_score else "관망"
    return (action if action == "의견 보류" else result), score


def format_history(context):
    lines = ["• 3년 고점 비교: 최근부터 서로 겹치지 않는 52주 3구간 (장중 고가 기준)"]
    current = context.get("current_relative")
    if current:
        lines.append(f"  현재 20거래일: 종목 {current['stock_return']:+.1%} / 코스피 {current['index_return']:+.1%} / 초과수익 {current['excess'] * 100:+.1f}%p")
    for number, row in enumerate(context["periods"], 1):
        lines.append(f"  {number}. {row['start']}~{row['end']}")
        if "high" in row:
            lines.append(f"     최고가 {row['high']:,}원 ({row['day']}) / 현재가의 고점 대비 {row['distance']:+.1%}")
        if "market" in row:
            market = row["market"]
            lines.append(f"     당시 코스피 {market['close']:,.2f} · {market['phase']} / 현재와 추세 점수 차이 {row['market_signal_change']:+d}")
        if "relative" in row:
            r = row["relative"]
            lines.append(f"     당시 20거래일: 종목 {r['stock_return']:+.1%} / 코스피 {r['index_return']:+.1%} / 초과수익 {r['excess'] * 100:+.1f}%p")
        if "missing" in row:
            lines.append(f"     비교 제외: {row['missing']}")
    lines.append(f"  3년 비교 점수 {context['signal']:+d}: {context['reason']}")
    return "\n".join(lines)
