"""Persist safe application diagnostics for time-based chat queries."""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
import inspect
import json
from contextvars import ContextVar
from functools import wraps
from time import perf_counter
from uuid import uuid4
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.models import DataError

KST = ZoneInfo("Asia/Seoul")
# Only diagnostics designed for chat are persisted. Provider/HTTP logs can contain secrets.
SAFE_PREFIXES = {
    "app.scheduler": ("Daily scheduler started:", "Daily scheduler result:"),
    "app.models": ("Strategy validation failed:",),
    "app.briefings": (
        "Briefing started:", "Briefing saved:", "Briefing withheld:",
        "Holdings briefing withheld:", "Watchlist analysis started:",
        "Watchlist opinion withheld:", "Watchlist opinion ready:", "Refresh job failed",
    ),
    "app.telegram": ("Telegram poll failed",),
    "app.logs": ("Application log capture started:", "Feature event:", "Python exception:"),
}


def safe_traceback(exc, seen=None):
    """Keep stack locations, never exception text, source lines or local values."""
    seen = set() if seen is None else seen
    if id(exc) in seen:
        return "[exception chain cycle]"
    seen.add(id(exc))
    lines = []
    cause = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    if cause is not None:
        lines.append(safe_traceback(cause, seen))
        lines.append("Caused by / during handling:")
    lines.append("Traceback (most recent call last):")
    frames = []
    tb = exc.__traceback__
    while tb is not None:
        code = tb.tb_frame.f_code
        frames.append(f'  File "{Path(code.co_filename).name}", line {tb.tb_lineno}, in {code.co_name}')
        tb = tb.tb_next
    if len(frames) > 30:
        lines.append("  [earlier frames omitted]")
    lines.extend(frames[-30:])
    lines.append(f"{type(exc).__name__}: [exception message omitted]")
    return "\n".join(lines)[-12000:]


def log_python_exception(source, exc):
    logging.getLogger("app.logs").error(
        "Python exception: source=%s error_type=%s", source, type(exc).__name__,
        exc_info=(type(exc), exc, exc.__traceback__),
    )


class StoredLogHandler(logging.Handler):
    def __init__(self, store):
        super().__init__(logging.INFO)
        self.store = store

    def emit(self, record):
        prefixes = SAFE_PREFIXES.get(record.name, ())
        safe_message = isinstance(record.msg, str) and record.msg.startswith(prefixes)
        if not safe_message and record.levelno < logging.ERROR:
            return
        try:
            at = datetime.fromtimestamp(record.created, timezone.utc)
            if safe_message:
                message = record.getMessage().replace("\r", " ").replace("\n", " ")[:2000]
            else:
                message = (f"Python log: {Path(record.pathname).name}:{record.lineno} "
                           f"in {record.funcName} [message omitted]")
            if record.exc_info and record.exc_info[1] is not None:
                message += "\n" + safe_traceback(record.exc_info[1])
            trace = _TRACE.get()
            if trace and not (safe_message and record.msg.startswith("Feature event:")):
                message = f"trace={trace} {message}"
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
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, StoredLogHandler) and handler.store.path == store.path:
            return
    logging.getLogger("app").setLevel(logging.INFO)
    root.addHandler(StoredLogHandler(store))
    # Uvicorn uses a non-propagating error logger.
    logging.getLogger("uvicorn.error").addHandler(StoredLogHandler(store))
    previous_sys = sys.excepthook
    previous_thread = threading.excepthook

    def unhandled(kind, exc, tb):
        log_python_exception("sys.excepthook", exc)
        previous_sys(kind, exc, tb)

    def thread_unhandled(args):
        log_python_exception("threading.excepthook", args.exc_value)
        previous_thread(args)

    sys.excepthook = unhandled
    threading.excepthook = thread_unhandled
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


_TRACE = ContextVar("feature_log_trace", default=None)


def feature_event(feature, status, **fields):
    """Only callers' explicitly selected diagnostics belong here, never raw input."""
    logging.getLogger("app.logs").info(
        "Feature event: %s",
        json.dumps({"trace": _TRACE.get(), "feature": feature, "status": status,
                    **fields}, ensure_ascii=False, default=str),
    )


def traced(feature):
    """Trace sync/async operations without recording arguments or exception text."""
    def decorate(fn):
        signature = inspect.signature(fn)

        def begin(args, kwargs):
            token = _TRACE.set(_TRACE.get() or uuid4().hex[:12])
            started = perf_counter()
            fields = {}
            bound = signature.bind(*args, **kwargs)
            for key in ("symbol", "start", "end", "expected"):
                value = bound.arguments.get(key)
                if key == "symbol" and isinstance(value, str) and re.fullmatch(r"[0-9]{6}", value):
                    fields[key] = value
                elif key != "symbol" and isinstance(value, (datetime, date)):
                    fields[key] = value.isoformat()
            feature_event(feature, "started", **fields)
            return token, started

        def finish(started, result):
            fields = {"elapsed_ms": round((perf_counter() - started) * 1000, 1)}
            if isinstance(result, (list, dict)):
                fields["result_count"] = len(result)
            elif isinstance(result, bool):
                fields["accepted"] = result
            feature_event(feature, "completed", **fields)

        def failed(started, exc):
            if isinstance(exc, Exception):
                log_python_exception(feature, exc)
            feature_event(feature, "failed", error_type=type(exc).__name__,
                          elapsed_ms=round((perf_counter() - started) * 1000, 1))

        if inspect.iscoroutinefunction(fn):
            @wraps(fn)
            async def wrapper(*args, **kwargs):
                token, started = begin(args, kwargs)
                try:
                    result = await fn(*args, **kwargs)
                    finish(started, result)
                    return result
                except BaseException as exc:
                    failed(started, exc)
                    raise
                finally:
                    _TRACE.reset(token)
        else:
            @wraps(fn)
            def wrapper(*args, **kwargs):
                token, started = begin(args, kwargs)
                try:
                    result = fn(*args, **kwargs)
                    finish(started, result)
                    return result
                except BaseException as exc:
                    failed(started, exc)
                    raise
                finally:
                    _TRACE.reset(token)
        wrapper.__signature__ = inspect.signature(fn, eval_str=True)
        return wrapper
    return decorate

