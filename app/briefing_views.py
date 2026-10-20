"""Immutable briefing snapshots shared by Telegram summaries and detail buttons."""
from __future__ import annotations

from uuid import uuid4

ACTIONS = ("매수", "매도", "관망", "의견 보류")


def save_view(store, day, body, entries):
    view_id = uuid4().hex
    view = {"id": view_id, "day": str(day), "body": body, "entries": entries}
    store.put("briefing_views", view_id, view)
    store.put("briefing_view_days", str(day), {"id": view_id})
    return view


def daily_view(store, day):
    pointer = store.get("briefing_view_days", str(day), {})
    return store.get("briefing_views", pointer.get("id", ""))


def count(view, action):
    return len({entry["symbol"] for entry in view["entries"] if entry["action"] == action})


def summary(view):
    lines = [f"[오늘 브리핑: {view['day']}]", " · ".join(
        f"{action} {count(view, action)}개" for action in ACTIONS[:3])]
    if count(view, "의견 보류"):
        lines.append(f"의견 보류 {count(view, '의견 보류')}개 (데이터 확인 필요)")
    lines.append("버튼을 누르면 해당 의견의 전체 내용을 보여드립니다.")
    lines.append("종목 수 기준이며, 보유·관심 분석은 구분해서 표시합니다.")
    if view["entries"] and "의견·수량 보류" in view["body"]:
        lines.append("보유종목에 데이터 확인이 필요한 내용이 있습니다. 전체 보기에서 확인하세요.")
    if not view["entries"]:
        lines.append(view["body"])
    return "\n".join(lines)


def options(view):
    actions = list(ACTIONS[:3])
    if count(view, "의견 보류"):
        actions.append("의견 보류")
    return ([f"브리핑 분류 {view['id']} {action}" for action in actions] +
            [f"브리핑 분류 {view['id']} 전체"],
            [f"{action} {count(view, action)}개" for action in actions] + ["전체 보기"])


def details(view, action):
    if action == "전체":
        return f"[브리핑 전체: {view['day']}]\n{view['body']}"
    entries = [entry for entry in view["entries"] if entry["action"] == action]
    return f"[{view['day']} / {action} {count(view, action)}개]\n\n" + (
        "\n\n".join(f"[{entry['section']}]\n{entry['text']}" for entry in entries)
        or "해당 의견의 종목이 없습니다.")
