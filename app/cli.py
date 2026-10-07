from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import os
from datetime import date
from pathlib import Path

from app.config import Settings
from app.storage import Store
from app.telegram import inspect_user_ids, poll, send_briefing, send_test
from app.briefings import BriefingEngine
from app.models import Strategy


def main() -> None:
    parser = argparse.ArgumentParser(description="stock-briefing internal worker")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("poll")
    sub.add_parser("send-briefing")
    sub.add_parser("send-test")
    sub.add_parser("run-daily", help="collect, analyze, save and send today's briefing")
    sub.add_parser("generate-briefing", help="collect and save without sending")
    key = sub.add_parser(
        "create-master-key", help="create a 600-permission key outside the DB directory"
    )
    key.add_argument("--file", type=Path, required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("--file", type=Path, required=True)
    strategy = sub.add_parser(
        "import-strategy",
        help="local operator setup; validate a complete strategy JSON",
    )
    strategy.add_argument("--file", type=Path, required=True)
    sub.add_parser("inspect-telegram-id", help="bootstrap before starting the poller")
    seed = sub.add_parser(
        "save-briefing", help="internal integration test; no strategy calculation"
    )
    seed.add_argument("--date", type=date.fromisoformat, required=True)
    seed.add_argument("--file", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    store = Store(settings.db_path)
    if args.command == "create-master-key":
        from cryptography.fernet import Fernet

        path = args.file.resolve()
        if (
            settings.db_path.resolve().parent in path.parents
            or path == settings.db_path.resolve()
        ):
            parser.error("Keep the master key outside the DB/project directory")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as key_file:
            key_file.write(Fernet.generate_key())
        print("created")
        return
    store.init()
    if args.command == "poll":
        asyncio.run(poll(settings, store))
    elif args.command == "send-briefing":
        print(asyncio.run(send_briefing(settings, store)))
    elif args.command == "generate-briefing":
        print(BriefingEngine(settings, store).generate())
    elif args.command == "run-daily":
        from datetime import datetime
        from app.services import KST

        day = datetime.now(KST).date()
        owner = store.acquire_lease("daily-worker", 7200)
        if not owner:
            print("skipped: daily worker already running")
            return
        try:
            with store.connect() as db:
                sent = db.execute(
                    "SELECT status FROM delivery_runs WHERE briefing_date=? AND channel='telegram'",
                    (day.isoformat(),),
                ).fetchone()
            if sent:
                print("skipped: delivery is already recorded or awaiting review")
                return
            BriefingEngine(settings, store).generate()
            print(asyncio.run(send_briefing(settings, store)))
        finally:
            store.release_lease("daily-worker", owner)
    elif args.command == "backup":
        store.backup(args.file)
        print("backed up")
    elif args.command == "import-strategy":
        import json

        values = Strategy.parse(
            json.loads(args.file.read_text(encoding="utf-8"))
        ).dict()
        owner = store.acquire_lease("collection", 60)
        if not owner:
            raise RuntimeError("Collection is running; try again later")
        try:
            store.put("settings", "strategy", values)
            for symbol, _ in store.items("proposals"):
                store.delete("proposals", symbol)
            store.audit("strategy_import", "local operator")
        finally:
            store.release_lease("collection", owner)
    elif args.command == "send-test":
        asyncio.run(send_test(settings, store))
        print("sent")
    elif args.command == "inspect-telegram-id":
        print(
            "\n".join(asyncio.run(inspect_user_ids(settings)))
            or "no private messages yet"
        )
    elif args.command == "save-briefing":
        body = (
            sys.stdin.read()
            if str(args.file) == "-"
            else args.file.read_text(encoding="utf-8")
        )
        store.save_briefing(args.date, body)
        print("saved")


if __name__ == "__main__":
    main()
