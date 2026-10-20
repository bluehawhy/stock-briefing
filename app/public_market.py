from __future__ import annotations

from app.logs import traced, feature_event

from datetime import date, datetime, timedelta
from dataclasses import asdict, dataclass
from statistics import mean, pstdev
from decimal import Decimal
from xml.etree import ElementTree

import httpx

from app.models import Bar, DataError, Strategy, number, symbol_code


class PublicMarketProvider:
    """Public Naver daily chart data; no brokerage credentials or account access."""

    def __init__(self, client=None):
        self.client = client or httpx.Client(timeout=20, follow_redirects=True)
        self.owns_client = client is None

    def close(self):
        if self.owns_client:
            self.client.close()

    def daily_bars(self, symbol: str, end: date) -> list[Bar]:
        return self._daily_bars(symbol_code(symbol), end)

    def kospi_bars(self, end: date) -> list[Bar]:
        # Preserve index decimals as integer hundredths for Bar validation.
        return self._daily_bars("KOSPI", end, index=True)

    @traced("public_market.daily_bars")
    def _daily_bars(self, symbol: str, end: date, *, index: bool = False) -> list[Bar]:
        try:
            response = self.client.get(
                "https://fchart.stock.naver.com/sise.nhn",
                params={
                    "symbol": symbol, "timeframe": "day",
                    "count": "1200", "requestType": "0",
                },
            )
            feature_event("public_market.http", "response", http_status=response.status_code)
            response.raise_for_status()
            if len(response.content) > 2_000_000:
                raise DataError("공개 일봉 응답 크기 초과")
            root = ElementTree.fromstring(response.content.decode("euc-kr"))
            bars = {}
            for item in root.iter("item"):
                fields = item.attrib["data"].split("|")
                if len(fields) != 6:
                    raise ValueError
                day = datetime.strptime(fields[0], "%Y%m%d").date()
                if day > end:
                    continue
                prices = [number(Decimal(value) * 100) if index else number(value) for value in fields[1:5]]
                bar = Bar(day, *prices, number(fields[5]))
                bar.validate()
                if day in bars and bars[day] != bar:
                    raise ValueError
                bars[day] = bar
            if not bars:
                raise DataError("공개 일봉 데이터가 없습니다.")
            return sorted(bars.values(), key=lambda bar: bar.day)
        except httpx.HTTPError:
            raise DataError("공개 일봉 조회 실패: 네트워크·제공처 상태를 확인하세요.") from None
        except (ElementTree.ParseError, KeyError, ValueError, TypeError) as exc:
            if isinstance(exc, DataError):
                raise
            raise DataError("공개 일봉 응답 형식 오류") from None


@dataclass(frozen=True)
class WatchStrategy:
    """Price/volume policy independent of account and brokerage configuration."""
    short_ma: int = 5
    long_ma: int = 20
    volume_window: int = 20
    volume_ratio: float = 1.5
    range_buy_max: float = 0.2
    range_sell_min: float = 0.8
    buy_score: int = 2
    sell_score: int = -2

    @property
    def weights(self):
        return dict.fromkeys(("ma_trend", "price_position", "cross", "range_position", "volume"), 1)

    def dict(self):
        return {**asdict(self), "weights": self.weights}


@traced("public_market.watch_opinion")
def watch_opinion(bars: list[Bar], expected: date, strategy, trading: list[date]):
    bars = sorted((b for b in bars if b.day <= expected), key=lambda b: b.day)
    if not bars or bars[-1].day != expected:
        raise DataError("공개 일봉 기준 거래일 불일치")
    for bar in bars:
        bar.validate()
    # Missing sessions cannot be replaced by adjacent observations.
    actual = {b.day for b in bars}
    if not set(d for d in trading if bars[0].day <= d <= expected).issubset(actual):
        raise DataError("공개 일봉 거래일 누락")
    close = bars[-1].close
    values = {"close": close, "missing": []}
    signals = {}
    for window in sorted({5, 20, 60, 120, strategy.short_ma, strategy.long_ma}):
        if len(bars) >= window:
            values[f"ma_{window}"] = mean(b.close for b in bars[-window:])
    if len(bars) >= strategy.long_ma + 1:
        short = values[f"ma_{strategy.short_ma}"]
        long = values[f"ma_{strategy.long_ma}"]
        ps = mean(b.close for b in bars[-strategy.short_ma - 1:-1])
        pl = mean(b.close for b in bars[-strategy.long_ma - 1:-1])
        cross = "상향" if ps <= pl and short > long else "하향" if ps >= pl and short < long else "없음"
        values.update(short_ma=short, long_ma=long, cross=cross)
        trend = (short > long) - (short < long)
        signals.update(ma_trend=trend, price_position=(close > long) - (close < long),
                       cross=1 if cross == "상향" else -1 if cross == "하향" else 0)
    else:
        values["missing"].append("이동평균 교차")
    if len(bars) >= 2:
        values["daily_return"] = close / bars[-2].close - 1
    for label, window in (("1주", 5), ("1개월", 20), ("3개월", 60)):
        if len(bars) > window:
            values[f"return_{label}"] = close / bars[-window - 1].close - 1
    if len(bars) > strategy.volume_window:
        average = mean(b.volume for b in bars[-strategy.volume_window - 1:-1])
        if average > 0 and bars[-1].volume > 0:
            values["volume_ratio"] = bars[-1].volume / average
            if "ma_trend" in signals:
                signals["volume"] = signals["ma_trend"] if values["volume_ratio"] >= strategy.volume_ratio else 0
    if "volume_ratio" not in values:
        values["missing"].append("거래량 비율(거래정지·이력 확인 필요)")
    start = expected - timedelta(days=364)
    if bars[0].day <= start:
        year = [b for b in bars if b.day >= start]
        low, high = min(b.low for b in year), max(b.high for b in year)
        values.update(low_52w=low, high_52w=high)
        if high > low:
            position = (close - low) / (high - low)
            values["range_position"] = position
            signals["range_position"] = 1 if position <= strategy.range_buy_max else -1 if position >= strategy.range_sell_min else 0
    else:
        values["missing"].append("52주 가격 위치")
    if len(bars) >= 21:
        values["high_20"] = max(b.high for b in bars[-21:-1])
        values["low_20"] = min(b.low for b in bars[-21:-1])
        returns = [b.close / a.close - 1 for a, b in zip(bars[:-1], bars[1:])]
        values["volatility"] = pstdev(returns[-20:])
        if len(returns) >= 40:
            values["volatility_previous"] = pstdev(returns[-40:-20])
    score = sum(signal * strategy.weights[key] for key, signal in signals.items())
    action = "매수" if score >= strategy.buy_score else "매도" if score <= strategy.sell_score else "관망"
    if "ma_trend" not in signals or "volume" not in signals:
        action = "의견 보류"
    return values, action, score


def format_watch(name: str, symbol: str, day: date, values: dict, action: str, strategy) -> str:
    close = values["close"]
    lines = [f"{name} ({symbol})", f"기준: {day} 종가 (공개 일봉)", "",
             "의견: " + {"매수": "매수 검토", "매도": "매도 검토"}.get(action, action)]
    from app.analysis import format_market
    lines.append(format_market(values["market"]))
    price = f"• 가격: {close:,}원"
    if "daily_return" in values:
        price += f" / 전일 대비 {values['daily_return']:+.1%}"
    lines.append(price)
    positions = [f"{w}일선 {'위' if close > values[f'ma_{w}'] else '아래' if close < values[f'ma_{w}'] else '동일'}"
                 for w in (5, 20, 60, 120) if f"ma_{w}" in values]
    if positions:
        lines.append("• 추세: " + ", ".join(positions))
    if "cross" in values:
        lines.append(f"• 교차: {strategy.short_ma}/{strategy.long_ma}일선 {values['cross']}")
    if "volume_ratio" in values:
        lines.append(f"• 거래량: 직전 {strategy.volume_window}일 평균 대비 {values['volume_ratio']:.2f}배")
    if "high_52w" in values:
        lines.append(f"• 가격 위치: 52주 최고 {values['high_52w']:,}원 대비 {close / values['high_52w'] - 1:+.1%}, 최저 {values['low_52w']:,}원 대비 {close / values['low_52w'] - 1:+.1%}")
    recent = [f"{label} {values[f'return_{label}']:+.1%}" for label in ("1주", "1개월", "3개월") if f"return_{label}" in values]
    if recent:
        lines.append("• 최근 흐름: " + ", ".join(recent))
    if "history_3y" in values:
        from app.watch_history import format_history

        lines.append(format_history(values["history_3y"]))
    cautions = []
    if "high_20" in values:
        status = "고점 돌파" if close > values["high_20"] else "저점 이탈" if close < values["low_20"] else "범위 내"
        lines.append(f"• 최근 20일: {values['low_20']:,}~{values['high_20']:,}원 / {status}")
        if status == "저점 이탈":
            cautions.append("최근 저점 이탈")
    if "volatility" in values:
        lines.append(f"• 변동성: 최근 20일 일간 수익률 표준편차 {values['volatility']:.1%}")
        if values["volatility"] > values.get("volatility_previous", float("inf")):
            cautions.append("직전 20일 대비 변동성 확대")
    if values.get("range_position", 0) >= strategy.range_sell_min:
        cautions.append("52주 고점 부근")
    cautions.extend(f"{item} 확인 불가·판단 제외" for item in values["missing"])
    lines.append("• 주의점: " + (", ".join(cautions) or "추가 경고 신호 없음"))
    return "\n".join(lines)
