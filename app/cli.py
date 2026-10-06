from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

from app.config import Settings
from app.storage import Store
from app.telegram import inspect_user_ids, poll, send_briefing, send_test


def main() -> None:
    parser = argparse.ArgumentParser(description="stock-briefing internal worker")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("poll")
    sub.add_parser("send-briefing")
    sub.add_parser("send-test")
    sub.add_parser("inspect-telegram-id", help="bootstrap before starting the poller")
    seed = sub.add_parser("save-briefing", help="internal integration test; no strategy calculation")
    seed.add_argument("--date", type=date.fromisoformat, required=True)
    seed.add_argument("--file", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    store = Store(settings.db_path)
    store.init()
    if args.command == "poll":
        asyncio.run(poll(settings, store))
    elif args.command == "send-briefing":
        print(asyncio.run(send_briefing(settings, store)))
    elif args.command == "send-test":
        asyncio.run(send_test(settings, store))
        print("sent")
    elif args.command == "inspect-telegram-id":
        print("\n".join(asyncio.run(inspect_user_ids(settings))) or "no private messages yet")
    elif args.command == "save-briefing":
        body = sys.stdin.read() if str(args.file) == "-" else args.file.read_text(encoding="utf-8")
        store.save_briefing(args.date, body)
        print("saved")


if __name__ == "__main__":
    main()
