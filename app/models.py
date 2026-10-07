from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal


class DataError(ValueError):
    """Safe, user-visible data error; never includes a provider response or secret."""


def symbol_code(value: str) -> str:
    if len(value) != 6 or not value.isascii() or not value.isdigit():
        raise DataError("종목코드는 숫자 6자리로 입력하세요.")
    return value


def number(value: object, *, negative: bool = False) -> int:
    # Reject fractional, NaN and boolean values rather than silently rounding.
    if isinstance(value, bool):
        raise DataError("숫자 형식 오류")
    try:
        parsed = Decimal(str(value).replace(",", ""))
        if not parsed.is_finite() or parsed != parsed.to_integral_value():
            raise ValueError
        result = int(parsed)
    except (ValueError, ArithmeticError):
        raise DataError("숫자 형식 오류") from None
    if not negative and result < 0:
        raise DataError("음수는 사용할 수 없습니다.")
    return result


@dataclass(frozen=True)
class Bar:
    day: date
    open: int
    high: int
    low: int
    close: int
    volume: int

    def validate(self) -> None:
        if any(
            isinstance(v, bool) or not isinstance(v, int)
            for v in (self.open, self.high, self.low, self.close, self.volume)
        ):
            raise DataError("일봉 숫자 형식 오류")
        if (
            not 0
            < self.low
            <= min(self.open, self.close)
            <= max(self.open, self.close)
            <= self.high
        ):
            raise DataError("일봉 가격 정합성 오류")
        if self.volume < 0:
            raise DataError("거래량 오류")


@dataclass(frozen=True)
class Flow:
    day: date
    institution: int
    foreign: int


@dataclass(frozen=True)
class Strategy:
    # No implicit investment policy: every field must be explicitly configured.
    short_ma: int
    long_ma: int
    volume_window: int
    flow_window: int
    volume_ratio: float
    range_buy_max: float
    range_sell_min: float
    buy_score: int
    sell_score: int
    buy_budget: int
    max_position: int
    sell_fraction: float
    holdings_max_age_hours: float
    evaluation_days: int
    fee_bps: float
    tax_bps: float
    slippage_bps: float
    weights: dict[str, int]

    @classmethod
    def parse(cls, values: dict) -> Strategy:
        try:
            if set(values) != set(cls.__dataclass_fields__):
                raise ValueError
            integer_fields = {
                "short_ma",
                "long_ma",
                "volume_window",
                "flow_window",
                "buy_score",
                "sell_score",
                "buy_budget",
                "max_position",
                "evaluation_days",
            }
            parsed = {
                k: number(v, negative=k == "sell_score")
                if k in integer_fields
                else float(v)
                for k, v in values.items()
                if k != "weights"
            }
            import math

            if any(not math.isfinite(v) for v in parsed.values()):
                raise ValueError
            weight_keys = {
                "ma_trend",
                "price_position",
                "cross",
                "range_position",
                "volume",
                "institution",
                "foreign",
            }
            if (
                not isinstance(values["weights"], dict)
                or set(values["weights"]) != weight_keys
            ):
                raise ValueError
            weights = {k: number(v) for k, v in values["weights"].items()}
            maximum_score = sum(weights.values())
            if maximum_score == 0 or any(v > 100 for v in weights.values()):
                raise ValueError
            parsed["weights"] = weights
            s = cls(**parsed)
            if not (
                1 <= s.short_ma < s.long_ma <= 250
                and 1 <= s.volume_window <= 250
                and 1 <= s.flow_window <= 30
                and s.volume_ratio > 0
                and 0 <= s.range_buy_max < s.range_sell_min <= 1
                and 1 <= s.buy_score <= maximum_score
                and -maximum_score <= s.sell_score <= -1
                and 0 < s.sell_fraction <= 1
                and 0 < s.holdings_max_age_hours <= 8760
                and 1 <= s.evaluation_days <= 250
                and all(0 <= v < 10000 for v in (s.fee_bps, s.tax_bps, s.slippage_bps))
            ):
                raise ValueError
            return s
        except (TypeError, ValueError, OverflowError):
            raise DataError(
                "전략 설정이 누락되었거나 범위를 벗어났습니다. 설정 도움말을 확인하세요."
            ) from None

    def dict(self) -> dict:
        return asdict(self)
