"""Compact presentation of this run's observed watchlist results."""

from collections import Counter


def watch_highlights(records: list[dict]) -> str:
    if not records:
        return ""
    counts = Counter(record["action"] for record in records)
    withheld = len(records) - sum(counts[action] for action in ("매수", "매도", "관망"))
    lines = [
        "📌 관심종목 한눈에",
        f"총 {len(records)}종목 · 매수 검토 {counts['매수']} · 매도 검토 {counts['매도']}"
        f" · 관망 {counts['관망']} · 의견 보류 {withheld}",
    ]
    observed = [record for record in records if "daily_return" in record.get("indicators", {})]
    for label, candidates, reverse in (
        ("상승", [r for r in observed if r["indicators"]["daily_return"] > 0], True),
        ("하락", [r for r in observed if r["indicators"]["daily_return"] < 0], False),
    ):
        if candidates:
            ranked = sorted(candidates, key=lambda r: (
                -r["indicators"]["daily_return"] if reverse else r["indicators"]["daily_return"],
                r["symbol"],
            ))
            record = ranked[0]
            lines.append(f"• 전일 대비 최대 {label}: {record['name']} ({record['symbol']}) "
                         f"{record['indicators']['daily_return']:+.1%}")
    events = []
    for record in records:
        values = record.get("indicators", {})
        signals = []
        if values.get("cross") in ("상향", "하향"):
            signals.append(f"이동평균 {values['cross']} 교차")
        close = values.get("close")
        if close is not None:
            if "high_20" in values and close > values["high_20"]:
                signals.append("20일 고점 돌파")
            if "low_20" in values and close < values["low_20"]:
                signals.append("20일 저점 이탈")
        if signals:
            events.append(f"• {record['name']} ({record['symbol']}): {', '.join(signals)}")
    lines.append("🔎 확인할 신호")
    lines.extend(events or ["• 확인된 교차·돌파·이탈 신호 없음"])
    lines.append("등록 관심종목 내 비교 · 종목별 기준일은 상세 참조 · 누락 지표는 신호 집계 제외")
    return "\n".join(lines)
