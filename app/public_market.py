from __future__ import annotations

from app.logs import traced, feature_event

from datetime import date, datetime
from xml.etree import ElementTree

import httpx

from app.analysis import indicators, opinion
from app.models import Bar, DataError, Strategy, number, symbol_code


class PublicMarketProvider:
    """Public Naver daily chart data; no brokerage credentials or account access."""

    def __init__(self, client=None):
        self.client = client or httpx.Client(timeout=20, follow_redirects=True)
        self.owns_client = client is None

    def close(self):
        if self.owns_client:
            self.client.close()

    @traced("public_market.daily_bars")
    def daily_bars(self, symbol: str, end: date) -> list[Bar]:
        try:
            response = self.client.get(
                "https://fchart.stock.naver.com/sise.nhn",
                params={
                    "symbol": symbol_code(symbol), "timeframe": "day",
                    "count": "600", "requestType": "0",
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
                bar = Bar(day, *(number(value) for value in fields[1:]))
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


@traced("public_market.watch_opinion")
def watch_opinion(bars: list[Bar], expected: date, strategy: Strategy, trading: list[date]):
    values = indicators(bars, [], expected, strategy, trading, include_flows=False)
    action, score = opinion(values, strategy, include_flows=False)
    return values, action, score


def format_watch(name: str, symbol: str, day: date, values: dict, action: str, strategy: Strategy) -> str:
    trend = (
        "상승" if values["short_ma"] > values["long_ma"]
        else "하락" if values["short_ma"] < values["long_ma"] else "횡보"
    )
    price_position = (
        "위" if values["close"] > values["long_ma"]
        else "아래" if values["close"] < values["long_ma"] else "동일"
    )
    return (
        f"{name} ({symbol}) / 기준 {day}\n의견: {action}\n"
        f"고려한 추세: {strategy.short_ma}/{strategy.long_ma}일 이평 {trend} "
        f"({values['short_ma']:,.1f}/{values['long_ma']:,.1f}), 교차 {values['cross']}\n"
        f"종가 {values['close']:,}원: 장기 이평 {price_position}\n"
        f"52주 {values['low_52w']:,}~{values['high_52w']:,}원, "
        f"위치 {values['range_position']:.1%}, 전일 위치 변화 {values['range_change']:+.1%}\n"
        f"거래량: 직전 {strategy.volume_window}일 평균 대비 {values['volume_ratio']:.2f}배"
    )
