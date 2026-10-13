"""Persist safe application diagnostics for time-based chat queries."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.models import DataError

KST = ZoneInfo("Asia/Seoul")
# Only diagnostics designed for chat are persisted. Provider/HTTP logs can contain secrets.
SAFE_PREFIXES = {
    "app.models": ("Strategy validation failed:",),
    "app.briefings": (
        "Briefing started:", "Briefing saved:", "Briefing withheld:",
        "Holdings briefing withheld:", "Watchlist analysis started:",
        "Watchlist opinion withheld:", "Watchlist opinion ready:", "Refresh job failed",
    ),
    "app.telegram": ("Telegram poll failed",),
    "app.logs": ("Application log capture started:",),
}


class StoredLogHandler(logging.Handler):
    def __init__(self, store):
        super().__init__(logging.INFO)
        self.store = store

    def emit(self, record):
        prefixes = SAFE_PREFIXES.get(record.name, ())
        if not isinstance(record.msg, str) or not record.msg.startswith(prefixes):
            return
        try:
            at = datetime.fromtimestamp(record.created, timezone.utc)
            message = record.getMessage().replace("\r", " ").replace("\n", " ")[:2000]
            with self.store.connect() as db:
                db.execute(
                    "INSERT INTO application_logs(created_at,level,source,message) VALUES (?,?,?,?)",
                    (at.isoformat(), record.levelname, record.name, message),
                )
                db.execute(
                    "DELETE FROM application_logs WHERE created_at<?",
                    ((at - timedelta(days=7)).isoformat(),),
                )
        except Exception:
            # Diagnostic storage must never interrupt briefing generation or recurse.
            pass


def install_log_capture(store):
    store.init()
    logger = logging.getLogger("app")
    for handler in logger.handlers:
        if isinstance(handler, StoredLogHandler) and handler.store.path == store.path:
            return
    logger.setLevel(logging.INFO)
    logger.addHandler(StoredLogHandler(store))
    logging.getLogger(__name__).info("Application log capture started: retention_days=7")


def log_window(argument, now):
    """Return a half-open UTC range and a Korean description."""
    if not argument:
        argument = "3분"
    match = re.fullmatch(r"([0-9]+)\s*분", argument)
    if match:
        minutes = int(match[1])
        if not 1 <= minutes <= 10080:
            raise DataError("로그 조회는 1~10080분(7일) 범위로 입력하세요.")
        end = now.astimezone(timezone.utc)
        return end - timedelta(minutes=minutes), end, f"최근 {minutes}분"
    if re.fullmatch(r"[0-9]{2}:[0-9]{2}", argument):
        hour, minute = map(int, argument.split(":"))
        if hour > 23 or minute > 59:
            raise DataError("시각은 00:00~23:59로 입력하세요.")
        start = now.astimezone(KST).replace(hour=hour, minute=minute, second=0, microsecond=0)
        end = start + timedelta(minutes=1)
        return (
            start.astimezone(timezone.utc),
            end.astimezone(timezone.utc),
            f"오늘 {argument}분 ({start:%Y-%m-%d})",
        )
    raise DataError("사용법: 로그 보기 / 로그 보기 10분 / 로그 보기 09:05")


def view_logs(store, argument="", *, now=None):
    now = now or datetime.now(timezone.utc)
    start, end, label = log_window(argument.strip(), now)
    store.init()
    with store.connect() as db:
        rows = db.execute(
            "SELECT created_at,level,source,message FROM application_logs "
            "WHERE created_at>=? AND created_at<? ORDER BY created_at DESC,id DESC LIMIT 201",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    header = f"[로그: {label} · 한국 시간]\n조회 구간: {start.astimezone(KST):%Y-%m-%d %H:%M:%S} ~ {end.astimezone(KST):%Y-%m-%d %H:%M:%S}"
    if not rows:
        return header + "\n해당 구간에 기록된 로그가 없습니다. 업데이트 이후부터 기록합니다."
    lines = []
    length = 0
    for at, level, source, message in rows[:200]:
        stamp = datetime.fromisoformat(at).astimezone(KST)
        line = f"{stamp:%Y-%m-%d %H:%M:%S} [{level}] {source}: {message}"
        if length + len(line) > 24000:
            break
        lines.append(line)
        length += len(line) + 1
    suffix = ""
    if len(rows) > len(lines):
        suffix = "\n로그가 많아 최신 일부를 표시했습니다. 더 짧은 분 범위나 HH:mm으로 조회하세요."
    return header + "\n" + "\n".join(reversed(lines)) + suffix
