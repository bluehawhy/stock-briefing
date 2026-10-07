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

    settings = Settings(
        tmp_path / "stock-briefing.db",
        "secret",
        frozenset(),
        "fake-token",
        frozenset({"42"}),
    )
    store = Store(settings.db_path)
    store.register_telegram("42", "42")
    assert (
        asyncio.run(send_briefing(settings, store))
        == "skipped: no briefing saved for today"
    )


def test_successful_delivery_is_once_and_ambiguous_failure_requires_review(
    settings, store, monkeypatch
):
    day = datetime.now(ZoneInfo("Asia/Seoul")).date()
    store.save_briefing(day, "자동 생성된 브리핑")
    store.register_telegram("42", "42")
    calls = []

    async def success(self, chat, text):
        calls.append((chat, text))

    # No network clients in unit tests, including environment proxy discovery.
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr("app.telegram.httpx.AsyncClient", lambda **kwargs: FakeClient())
    monkeypatch.setattr(TelegramAPI, "send_text", success)
    assert asyncio.run(send_briefing(settings, store)) == "sent"
    assert "already recorded" in asyncio.run(send_briefing(settings, store))
    assert len(calls) == 1
    assert store.last_delivery()[1] == "sent"
    with store.connect() as db:
        db.execute("DELETE FROM delivery_runs")

    async def failure(self, chat, text):
        raise RuntimeError("token-bearing URL is never persisted")

    monkeypatch.setattr(TelegramAPI, "send_text", failure)
    import pytest

    with pytest.raises(RuntimeError, match="review"):
        asyncio.run(send_briefing(settings, store))
    assert store.last_delivery()[1] == "review_required"
    assert "already recorded" in asyncio.run(send_briefing(settings, store))
    with store.connect() as db:
        assert (
            "token-bearing"
            not in db.execute("SELECT detail FROM delivery_runs").fetchone()[0]
        )


def test_notifications_off_and_unregistered_user(settings, store):
    day = datetime.now(ZoneInfo("Asia/Seoul")).date()
    store.save_briefing(day, "브리핑")
    assert "has not started" in asyncio.run(send_briefing(settings, store))
    store.register_telegram("42", "42")
    store.put("settings", "notifications", {"enabled": False})
    assert "disabled" in asyncio.run(send_briefing(settings, store))
    assert store.last_delivery() is None


def test_send_chunks_and_transport_error_redacts_token():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await TelegramAPI("fake-token", client).send_text("42", "x" * 9001)

    asyncio.run(run())
    assert len(requests) == 3
    import json

    assert all(len(json.loads(r.content)["text"]) <= 4000 for r in requests)

    def failure(request):
        raise httpx.ReadTimeout("https://api.telegram.org/botTOP-SECRET/sendMessage")

    async def fail():
        async with httpx.AsyncClient(transport=httpx.MockTransport(failure)) as client:
            await TelegramAPI("TOP-SECRET", client).send_text("42", "x")

    import pytest

    with pytest.raises(RuntimeError) as exc:
        asyncio.run(fail())
    assert "TOP-SECRET" not in str(exc.value)
