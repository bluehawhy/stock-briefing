import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.briefing_views import count, daily_view, details, save_view, summary
from app.commands import CommandNavigator
from app.services import BotService, InboundMessage
from app.telegram import (
    TelegramAPI, callback_command, process_update, reply_markup,
)


def message(text="메인", user="42", chat="42"):
    return InboundMessage("telegram", user, text, chat)


def snapshot(store):
    day = datetime.now(ZoneInfo("Asia/Seoul")).date()
    entries = [
        {"symbol": "005930", "action": "매수", "section": section, "text": section + " 매수 상세"}
        for section in ("보유종목", "관심종목")
    ] + [
        {"symbol": "000660", "action": "매도", "section": "관심종목", "text": "매도 상세"},
        {"symbol": "035420", "action": "관망", "section": "관심종목", "text": "관망 상세"},
        {"symbol": "035720", "action": "의견 보류", "section": "관심종목", "text": "데이터 부족"},
    ]
    return save_view(store, day, "원본 전체", entries)


def test_summary_counts_unique_stocks_and_keeps_withheld_separate(store):
    view = snapshot(store)
    assert count(view, "매수") == 1
    assert "매수 1개 · 매도 1개 · 관망 1개" in summary(view)
    assert "의견 보류 1개" in summary(view)
    body = details(view, "매수")
    assert "보유종목 매수 상세" in body and "관심종목 매수 상세" in body
    assert "매도 상세" not in body and "관망 상세" not in body
    assert "원본 전체" in details(view, "전체")


def test_today_generates_summary_and_old_buttons_keep_original_contents(settings, store, monkeypatch):
    view = snapshot(store)
    monkeypatch.setattr("app.briefings.BriefingEngine.generate", lambda self: view["body"])
    service = BotService(settings, store)
    inbound = message("오늘 브리핑")
    reply = service.handle(inbound)
    assert "매수 1개" in reply
    markup = reply_markup(store, inbound, reply)
    button = markup["inline_keyboard"][0][0]
    assert button["text"] == "매수 1개"
    assert len(button["callback_data"].encode()) <= 64
    save_view(store, view["day"], "새 브리핑", [])
    command = callback_command(store, inbound, button["callback_data"])
    result = service.handle(message(command))
    assert "보유종목 매수 상세" in result and "새 브리핑" not in result
    assert daily_view(store, view["day"])["body"] == "새 브리핑"


def test_menu_buttons_reject_replaced_expired_or_other_conversation(store, settings):
    service = BotService(settings, store)
    inbound = message("관심종목")
    reply = service.handle(inbound)
    data = reply_markup(store, inbound, reply)["inline_keyboard"][1][0]["callback_data"]
    assert callback_command(store, inbound, data) == "2"
    assert callback_command(store, message(user="other", chat="other"), data) is None
    service.handle(message("브리핑"))
    assert callback_command(store, inbound, data) is None
    selection = store.get("command_choices", CommandNavigator.key(inbound))
    data = f"choice:{selection['id']}:1"
    selection["expires_at"] = "2000-01-01T00:00:00+00:00"
    store.put("command_choices", CommandNavigator.key(inbound), selection)
    assert callback_command(store, inbound, data) is None


def test_callback_acknowledges_before_execution_and_checks_real_sender(settings, store):
    events = []

    class API:
        async def call(self, method, payload):
            events.append(method)

        async def send_text(self, chat, text, markup=None):
            events.append(text)

    class Service:
        def handle(self, inbound):
            events.append("execute:" + inbound.text)
            return "메뉴 응답"

    update = {"update_id": 1, "callback_query": {"id": "query", "data": "main",
        "from": {"id": 42}, "message": {"from": {"id": 999},
            "chat": {"id": 42, "type": "private"}}}}
    asyncio.run(process_update(API(), Service(), settings, store, update))
    assert events[:2] == ["answerCallbackQuery", "execute:메인"]
    events.clear()
    update["callback_query"]["from"]["id"] = 999
    asyncio.run(process_update(API(), Service(), settings, store, update))
    assert events == ["answerCallbackQuery"]


def test_long_details_are_sent_in_full_with_buttons_on_last_chunk():
    requests = []
    text = "종목 상세\n" * 2000
    markup = {"inline_keyboard": [[{"text": "전체", "callback_data": "main"}]]}

    def handler(request):
        import json
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await TelegramAPI("fake", client).send_text("42", text, markup)

    asyncio.run(run())
    assert "".join(request["text"] for request in requests) == text
    assert all(len(request["text"]) <= 4000 for request in requests)
    assert requests[-1]["reply_markup"] == markup
    assert "reply_markup" not in requests[0]


def test_missing_snapshot_and_empty_category_are_explicit(settings, store):
    view = save_view(store, "2026-10-20", "등록된 종목 없음", [])
    assert "해당 의견의 종목이 없습니다" in details(view, "매수")
    assert "등록된 종목 없음" in summary(view)
    service = BotService(settings, store)
    assert "해당 브리핑이 없습니다" in service.handle(message("브리핑 분류 " + "0" * 32 + " 매수"))


def test_scheduled_delivery_uses_same_snapshot_buttons(settings, store, monkeypatch):
    from app.telegram import send_briefing

    view = snapshot(store)
    day = datetime.now(ZoneInfo("Asia/Seoul")).date()
    store.save_briefing(day, view["body"])
    store.register_telegram("42", "42")
    sent = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    async def send(self, chat, text, markup=None):
        sent.append((chat, text, markup))

    monkeypatch.setattr("app.telegram.httpx.AsyncClient", lambda **kwargs: Client())
    monkeypatch.setattr(TelegramAPI, "send_text", send)
    assert asyncio.run(send_briefing(settings, store)) == "sent"
    assert "매수 1개" in sent[0][1]
    data = sent[0][2]["inline_keyboard"][0][0]["callback_data"]
    assert callback_command(store, message(), data) == f"브리핑 분류 {view['id']} 매수"
    assert "already recorded" in asyncio.run(send_briefing(settings, store))
    assert len(sent) == 1


def test_confirmation_button_preserves_existing_one_time_confirmation(settings, store):
    service = BotService(settings, store)
    inbound = message("보유량 설정 005930 10")
    reply = service.handle(inbound)
    markup = reply_markup(store, inbound, reply)
    data = markup["inline_keyboard"][0][0]["callback_data"]
    command = callback_command(store, inbound, data)
    assert store.get("manual_holdings", "005930") is None
    assert service.handle(message(command, user="other", chat="other")) is None
    assert store.get("manual_holdings", "005930") is None
    service.handle(message(command))
    assert store.get("manual_holdings", "005930")["quantity"] == 10
    assert "이미 사용" in service.handle(message(command))
