from __future__ import annotations

import logging
import math

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal


LOG = logging.getLogger(__name__)


class DataError(ValueError):
    """Safe, user-visible data error; never includes a provider response or secret."""


def symbol_code(value: str) -> str:
    if len(value) != 6 or not value.isascii() or not value.isalnum():
        raise DataError("종목코드는 영문·숫자 6자리로 입력하세요.")
    return value.upper()


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
        reason = "invalid_strategy"
        field = "-"
        try:
            if not isinstance(values, dict):
                reason = "expected_object"
                raise ValueError
            missing = sorted(set(cls.__dataclass_fields__) - set(values))
            if missing or set(values) != set(cls.__dataclass_fields__):
                LOG.warning(
                    "Strategy validation failed: reason=field_mismatch missing=%s unexpected_count=%d",
                    ",".join(missing) or "-",
                    len(set(values) - set(cls.__dataclass_fields__)),
                )
                reason = "field_mismatch"
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
            parsed = {}
            for k, v in values.items():
                if k == "weights":
                    continue
                field = k
                reason = "invalid_number"
                parsed[k] = (
                    number(v, negative=k == "sell_score")
                    if k in integer_fields else float(v)
                )
                if not math.isfinite(parsed[k]):
                    reason = "non_finite_number"
                    raise ValueError
            field = "weights"
            reason = "invalid_weight_fields"
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
            weights = {}
            for k, v in values["weights"].items():
                field = "weights." + k
                reason = "invalid_weight"
                weights[k] = number(v)
            maximum_score = sum(weights.values())
            if maximum_score == 0 or any(v > 100 for v in weights.values()):
                field = "weights"
                reason = "weight_range_or_zero_sum"
                raise ValueError
            parsed["weights"] = weights
            s = cls(**parsed)
            checks = {
                "short_ma,long_ma": 1 <= s.short_ma < s.long_ma <= 250,
                "volume_window": 1 <= s.volume_window <= 250,
                "flow_window": 1 <= s.flow_window <= 30,
                "volume_ratio": s.volume_ratio > 0,
                "range_buy_max,range_sell_min": 0 <= s.range_buy_max < s.range_sell_min <= 1,
                "buy_score": 1 <= s.buy_score <= maximum_score,
                "sell_score": -maximum_score <= s.sell_score <= -1,
                "sell_fraction": 0 < s.sell_fraction <= 1,
                "holdings_max_age_hours": 0 < s.holdings_max_age_hours <= 8760,
                "evaluation_days": 1 <= s.evaluation_days <= 250,
                "fee_bps": 0 <= s.fee_bps < 10000,
                "tax_bps": 0 <= s.tax_bps < 10000,
                "slippage_bps": 0 <= s.slippage_bps < 10000,
            }
            invalid = [key for key, valid in checks.items() if not valid]
            if invalid:
                field = ",".join(invalid)
                reason = "out_of_range"
                raise ValueError
            return s
        except (TypeError, ValueError, OverflowError):
            if reason != "field_mismatch":
                LOG.warning(
                    "Strategy validation failed: reason=%s field=%s", reason, field
                )
            raise DataError(
                "전략 설정이 누락되었거나 범위를 벗어났습니다. 설정 도움말을 확인하세요."
            ) from None

    def dict(self) -> dict:
        return asdict(self)
