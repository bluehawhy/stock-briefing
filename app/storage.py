from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def init(self) -> None:
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS telegram_recipients (
                    user_id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL UNIQUE,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS poll_state (
                    bot TEXT PRIMARY KEY,
                    next_update_id INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS briefings (
                    briefing_date TEXT PRIMARY KEY,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS delivery_runs (
                    briefing_date TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (briefing_date, channel)
                );
            """)

    def register_telegram(self, user_id: str, chat_id: str) -> None:
        self.init()
        with self.connect() as db:
            db.execute(
                """INSERT INTO telegram_recipients VALUES (?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET chat_id=excluded.chat_id,
                   updated_at=excluded.updated_at""",
                (user_id, chat_id, _now()),
            )

    def telegram_chat(self, user_id: str) -> str | None:
        self.init()
        with self.connect() as db:
            row = db.execute(
                "SELECT chat_id FROM telegram_recipients WHERE user_id=?", (user_id,)
            ).fetchone()
            return row[0] if row else None

    def poll_offset(self) -> int:
        self.init()
        with self.connect() as db:
            row = db.execute("SELECT next_update_id FROM poll_state WHERE bot='telegram'").fetchone()
            return row[0] if row else 0

    def set_poll_offset(self, offset: int) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO poll_state VALUES ('telegram', ?)
                   ON CONFLICT(bot) DO UPDATE SET next_update_id=excluded.next_update_id""",
                (offset,),
            )

    def save_briefing(self, day: date, body: str) -> None:
        if not body.strip():
            raise ValueError("Briefing body must not be empty")
        self.init()
        with self.connect() as db:
            db.execute(
                """INSERT INTO briefings VALUES (?, ?, ?)
                   ON CONFLICT(briefing_date) DO UPDATE SET
                   body=excluded.body, created_at=excluded.created_at""",
                (day.isoformat(), body, _now()),
            )

    def briefing(self, day: date | None = None) -> tuple[str, str] | None:
        self.init()
        with self.connect() as db:
            if day is None:
                row = db.execute(
                    "SELECT briefing_date, body FROM briefings ORDER BY briefing_date DESC LIMIT 1"
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT briefing_date, body FROM briefings WHERE briefing_date=?",
                    (day.isoformat(),),
                ).fetchone()
            return (row[0], row[1]) if row else None

    def last_delivery(self) -> tuple[str, str] | None:
        self.init()
        with self.connect() as db:
            row = db.execute(
                """SELECT briefing_date, status FROM delivery_runs
                   ORDER BY briefing_date DESC LIMIT 1"""
            ).fetchone()
            return (row[0], row[1]) if row else None

    def reserve_delivery(self, day: date) -> bool:
        self.init()
        with self.connect() as db:
            cur = db.execute(
                """INSERT OR IGNORE INTO delivery_runs
                   (briefing_date, channel, status, updated_at)
                   VALUES (?, 'telegram', 'pending', ?)""",
                (day.isoformat(), _now()),
            )
            return cur.rowcount == 1

    def finish_delivery(self, day: date, status: str, detail: str = "") -> None:
        with self.connect() as db:
            db.execute(
                """UPDATE delivery_runs SET status=?, detail=?, updated_at=?
                   WHERE briefing_date=? AND channel='telegram'""",
                (status, detail[:300], _now(), day.isoformat()),
            )
