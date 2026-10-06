import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings
from app.storage import Store
from app.telegram import TelegramAPI, send_briefing


def test_send_message_api_and_today_only(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async def send():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await TelegramAPI("fake-token", client).send_text("123", "테스트")

    asyncio.run(send())
    assert requests[0].url.path.endswith("/sendMessage")
    assert b'"chat_id":"123"' in requests[0].content

    settings = Settings(tmp_path / "stock-briefing.db", "secret", frozenset(), "fake-token", frozenset({"42"}))
    store = Store(settings.db_path)
    store.register_telegram("42", "42")
    assert asyncio.run(send_briefing(settings, store)) == "skipped: no briefing saved for today"
