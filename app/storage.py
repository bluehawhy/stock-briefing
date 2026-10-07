from __future__ import annotations

import sqlite3
import json
import secrets
import os
from contextlib import contextmanager
from datetime import date, datetime, timezone, timedelta
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._initialized = False

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("PRAGMA journal_mode=WAL")
            with db:
                yield db
        finally:
            db.close()

    def init(self) -> None:
        if self._initialized:
            return
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
                CREATE TABLE IF NOT EXISTS records (
                    kind TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                    updated_at TEXT NOT NULL, PRIMARY KEY(kind, key)
                );
                CREATE TABLE IF NOT EXISTS daily_bars (
                    symbol TEXT NOT NULL, day TEXT NOT NULL,
                    open INTEGER NOT NULL, high INTEGER NOT NULL, low INTEGER NOT NULL,
                    close INTEGER NOT NULL, volume INTEGER NOT NULL,
                    PRIMARY KEY(symbol, day)
                );
                CREATE TABLE IF NOT EXISTS investor_flows (
                    symbol TEXT NOT NULL, day TEXT NOT NULL,
                    institution INTEGER NOT NULL, foreign_net INTEGER NOT NULL,
                    PRIMARY KEY(symbol, day)
                );
                CREATE TABLE IF NOT EXISTS confirmations (
                    code TEXT PRIMARY KEY, channel TEXT NOT NULL, user_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL,
                    expires_at TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS leases (
                    name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL,
                    detail TEXT NOT NULL, created_at TEXT NOT NULL
                );
            """)
        self._initialized = True

    def get(self, kind: str, key: str, default=None):
        self.init()
        with self.connect() as db:
            row = db.execute(
                "SELECT value FROM records WHERE kind=? AND key=?", (kind, key)
            ).fetchone()
        return json.loads(row[0]) if row else default

    def items(self, kind: str) -> list[tuple[str, dict]]:
        self.init()
        with self.connect() as db:
            rows = db.execute(
                "SELECT key, value FROM records WHERE kind=? ORDER BY key", (kind,)
            ).fetchall()
        return [(key, json.loads(value)) for key, value in rows]

    def put(self, kind: str, key: str, value: dict) -> None:
        self.init()
        with self.connect() as db:
            self.put_in(db, kind, key, value)

    @staticmethod
    def put_in(db, kind: str, key: str, value: dict) -> None:
        db.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?) ON CONFLICT(kind,key) DO UPDATE SET "
            "value=excluded.value, updated_at=excluded.updated_at",
            (kind, key, json.dumps(value, ensure_ascii=False, allow_nan=False), _now()),
        )

    def delete(self, kind: str, key: str) -> None:
        self.init()
        with self.connect() as db:
            db.execute("DELETE FROM records WHERE kind=? AND key=?", (kind, key))

    def take(self, kind: str, key: str, expected=None):
        self.init()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT value FROM records WHERE kind=? AND key=?", (kind, key)
            ).fetchone()
            if row and expected is not None and json.loads(row[0]) != expected:
                return None
            db.execute("DELETE FROM records WHERE kind=? AND key=?", (kind, key))
        return json.loads(row[0]) if row else None

    def audit(self, action: str, detail: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO audit(action, detail, created_at) VALUES (?, ?, ?)",
                (action, detail, _now()),
            )

    def bars(self, symbol: str):
        from app.models import Bar

        self.init()
        with self.connect() as db:
            rows = db.execute(
                "SELECT day,open,high,low,close,volume FROM daily_bars WHERE symbol=? ORDER BY day",
                (symbol,),
            ).fetchall()
        return [Bar(date.fromisoformat(r[0]), *r[1:]) for r in rows]

    def flows(self, symbol: str):
        from app.models import Flow

        self.init()
        with self.connect() as db:
            rows = db.execute(
                "SELECT day,institution,foreign_net FROM investor_flows WHERE symbol=? ORDER BY day",
                (symbol,),
            ).fetchall()
        return [Flow(date.fromisoformat(r[0]), *r[1:]) for r in rows]

    def cache_market(self, symbol: str, bars=(), flows=()) -> None:
        self.init()
        for bar in bars:
            bar.validate()
        with self.connect() as db:
            db.executemany(
                "INSERT INTO daily_bars VALUES (?,?,?,?,?,?,?) ON CONFLICT(symbol,day) DO UPDATE SET "
                "open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close, volume=excluded.volume",
                [
                    (
                        symbol,
                        b.day.isoformat(),
                        b.open,
                        b.high,
                        b.low,
                        b.close,
                        b.volume,
                    )
                    for b in bars
                ],
            )
            db.executemany(
                "INSERT INTO investor_flows VALUES (?,?,?,?) ON CONFLICT(symbol,day) DO UPDATE SET "
                "institution=excluded.institution, foreign_net=excluded.foreign_net",
                [(symbol, f.day.isoformat(), f.institution, f.foreign) for f in flows],
            )

    def confirm(self, message, action: str, payload: dict) -> str:
        self.init()
        code = secrets.token_hex(8)
        expiry = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        with self.connect() as db:
            db.execute(
                "INSERT INTO confirmations VALUES (?,?,?,?,?,?,?,0)",
                (
                    code,
                    message.channel,
                    message.user_id,
                    message.chat_id or message.user_id,
                    action,
                    json.dumps(payload, ensure_ascii=False),
                    expiry,
                ),
            )
        return code

    def apply_confirmation(self, message, code: str, apply):
        """Consume and apply a local mutation atomically, including channel/conversation checks."""
        self.init()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT action,payload FROM confirmations WHERE code=? AND channel=? "
                "AND user_id=? AND chat_id=? AND used=0 AND expires_at>?",
                (
                    code,
                    message.channel,
                    message.user_id,
                    message.chat_id or message.user_id,
                    _now(),
                ),
            ).fetchone()
            if not row:
                return "확인 코드가 만료되었거나 이미 사용되었습니다. 요청한 대화에서 다시 시도하세요."
            result = apply(db, row[0], json.loads(row[1]))
            db.execute("UPDATE confirmations SET used=1 WHERE code=?", (code,))
            db.execute(
                "INSERT INTO audit(action,detail,created_at) VALUES (?,?,?)",
                (row[0], message.channel + ":" + message.user_id, _now()),
            )
            return result

    def enqueue_refresh(self) -> int:
        self.init()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id FROM jobs WHERE status IN ('queued','running') ORDER BY id LIMIT 1"
            ).fetchone()
            if row:
                return row[0]
            return db.execute(
                "INSERT INTO jobs(status,updated_at) VALUES ('queued',?)", (_now(),)
            ).lastrowid

    def claim_job(self) -> int | None:
        self.init()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # A crashed consumer is visible; it is not silently replayed.
            stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
            db.execute(
                "UPDATE jobs SET status='failed', detail='interrupted; request refresh again' "
                "WHERE status='running' AND updated_at<?",
                (stale,),
            )
            row = db.execute(
                "SELECT id FROM jobs WHERE status='queued' ORDER BY id LIMIT 1"
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE jobs SET status='running',updated_at=? WHERE id=?",
                    (_now(), row[0]),
                )
                return row[0]
        return None

    def finish_job(self, job: int, status: str, detail: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET status=?,detail=?,updated_at=? WHERE id=?",
                (status, detail, _now(), job),
            )

    def latest_job(self):
        self.init()
        with self.connect() as db:
            return db.execute(
                "SELECT id,status,detail FROM jobs ORDER BY id DESC LIMIT 1"
            ).fetchone()

    def acquire_lease(self, name: str, seconds: int = 3600) -> str | None:
        self.init()
        owner = secrets.token_hex(16)
        expiry = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "DELETE FROM leases WHERE name=? AND expires_at<=?", (name, _now())
            )
            cursor = db.execute(
                "INSERT OR IGNORE INTO leases VALUES (?,?,?)", (name, owner, expiry)
            )
        return owner if cursor.rowcount else None

    def release_lease(self, name: str, owner: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM leases WHERE name=? AND owner=?", (name, owner))

    def renew_lease(self, name: str, owner: str, seconds: int = 7200) -> bool:
        expiry = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE leases SET expires_at=? WHERE name=? AND owner=? AND expires_at>?",
                (expiry, name, owner, _now()),
            )
        return cursor.rowcount == 1

    def backup(self, destination: Path) -> None:
        destination = destination.resolve()
        if destination == self.path.resolve() or destination.exists():
            raise ValueError("Choose a new backup path")
        destination.parent.mkdir(parents=True, exist_ok=True)
        import os

        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with self.connect() as source, sqlite3.connect(destination) as target:
            source.backup(target)

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
            row = db.execute(
                "SELECT next_update_id FROM poll_state WHERE bot='telegram'"
            ).fetchone()
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
