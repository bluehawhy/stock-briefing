from __future__ import annotations

from app.logs import traced, feature_event

import asyncio
import logging
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings
from app.services import BotService, InboundMessage
from app.storage import Store
from app.commands import CommandNavigator, MENUS
from app.briefing_views import daily_view, options, summary

LOG = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


def choice_markup(selection):
    rows = []
    buttons = []
    for index, (command, label) in enumerate(zip(
        selection["options"], selection.get("labels", selection["options"])
    ), 1):
        match = re.fullmatch(r"브리핑 분류 ([0-9a-f]{32}) (매수|매도|관망|의견 보류|전체)", command)
        data = (f"brief:{match[1]}:{match[2]}" if match else
                f"choice:{selection['id']}:{index}")
        buttons.append({"text": label, "callback_data": data})
    # Short categories share a row; longer action labels retain their own button.
    columns = 2 if tuple(selection["options"]) == tuple(MENUS) else 1
    rows.extend(buttons[start:start + columns] for start in range(0, len(buttons), columns))
    rows.append([{"text": "메인", "callback_data": "main"},
                 {"text": "취소", "callback_data": "cancel"}])
    return {"inline_keyboard": rows}


def reply_markup(store, message, reply) -> dict | None:
    selection = store.get("command_choices", CommandNavigator.key(message))
    if selection and selection.get("id") and selection["expires_at"] > datetime.now(timezone.utc).isoformat():
        return choice_markup(selection)
    match = re.search(r"'(확인|주문 확인) ([0-9a-f]{16})'", reply)
    if match:
        return {"inline_keyboard": [[
            {"text": "확인", "callback_data": f"confirm:{match[2]}"}]]}
    return None


def callback_command(store, message, data):
    """Resolve opaque menu choices only in the authorized sender's conversation."""
    if data in {"main", "cancel"}:
        return "메인" if data == "main" else "취소"
    match = re.fullmatch(r"brief:([0-9a-f]{32}):(매수|매도|관망|의견 보류|전체)", data)
    if match:
        return f"브리핑 분류 {match[1]} {match[2]}"
    match = re.fullmatch(r"confirm:([0-9a-f]{16})", data)
    if match:
        return f"확인 {match[1]}"
    match = re.fullmatch(r"choice:([0-9a-f]{32}):([0-9]+)", data)
    if match:
        selection = store.get("command_choices", CommandNavigator.key(message))
        if (selection and selection.get("id") == match[1]
                and selection["expires_at"] > datetime.now(timezone.utc).isoformat()
                and 1 <= int(match[2]) <= len(selection["options"])):
            return match[2]
    return None


async def process_update(api, service, settings, store, update):
    callback = update.get("callback_query")
    message = (callback.get("message") if callback else update.get("message")) or {}
    user = (callback.get("from") if callback else message.get("from")) or {}
    chat = message.get("chat") or {}
    user_id, chat_id = str(user.get("id", "")), str(chat.get("id", ""))
    if callback:
        # Acknowledge promptly, before potentially blocking market collection.
        try:
            await api.call("answerCallbackQuery", {"callback_query_id": callback["id"]})
        except RuntimeError:
            LOG.warning("Telegram callback acknowledgement failed")
    if (user_id not in settings.telegram_allowed_user_ids or chat_id != user_id
            or chat.get("type") != "private"):
        return
    text = message.get("text", "")
    inbound = InboundMessage(channel="telegram", user_id=user_id, chat_id=chat_id,
                             text=text if isinstance(text, str) else "", is_private=True,
                             message_id=str(update["update_id"]))
    if callback:
        from dataclasses import replace

        text = callback_command(store, inbound, callback.get("data", ""))
        if text is None:
            await api.send_text(chat_id, "버튼이 만료되었거나 이미 선택되었습니다. 메인 메뉴에서 다시 선택하세요.")
            return
        inbound = replace(inbound, text=text)
    elif not isinstance(text, str) or not text:
        return
    reply = await asyncio.to_thread(service.handle, inbound)
    if reply:
        await api.send_text(chat_id, reply, reply_markup(store, inbound, reply))


class TelegramAPI:
    def __init__(self, token: str, client: httpx.AsyncClient):
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        self.token, self.client = token, client

    async def call(self, method: str, payload: dict) -> object:
        try:
            response = await self.client.post(
                f"https://api.telegram.org/bot{self.token}/{method}", json=payload
            )
        except httpx.HTTPError:
            # httpx's default exception can include the token-bearing URL.
            raise RuntimeError(f"Telegram {method} transport error") from None
        if method != "getUpdates" or response.status_code >= 400:
            feature_event("telegram.http", "response", method=method, http_status=response.status_code)
        if response.status_code >= 400:
            raise RuntimeError(f"Telegram {method} HTTP {response.status_code}")
        result = response.json()
        if not result.get("ok"):
            # Do not print the request URL: it contains the bot token.
            raise RuntimeError(
                f"Telegram {method} failed: {result.get('error_code', 'unknown')}"
            )
        return result["result"]

    @traced("telegram.send_text")
    async def send_text(self, chat_id: str, text: str, markup: dict | None = None) -> None:
        # Keep chunks under Telegram's 4096-character sendMessage limit.
        while text:
            chunk, text = text[:4000], text[4000:]
            payload = {"chat_id": chat_id, "text": chunk}
            if markup and not text:
                payload["reply_markup"] = markup
            await self.call("sendMessage", payload)


async def poll(settings: Settings, store: Store) -> None:
    service = BotService(settings, store)
    store.init()
    async with httpx.AsyncClient(timeout=httpx.Timeout(35.0)) as client:
        api = TelegramAPI(settings.telegram_bot_token, client)
        while True:
            try:
                updates = await api.call(
                    "getUpdates",
                    {
                        "offset": store.poll_offset(),
                        "timeout": 30,
                        "allowed_updates": ["message", "callback_query"],
                    },
                )
                for update in updates:
                    update_id = update["update_id"]
                    await process_update(api, service, settings, store, update)
                    # Advance only after processing; a crash can replay a read-only command.
                    store.set_poll_offset(update_id + 1)
            except (httpx.HTTPError, RuntimeError, KeyError, ValueError) as exc:
                # httpx exceptions may include a URL with a bot token; log only the type.
                LOG.warning("Telegram poll failed (%s); retrying", type(exc).__name__, exc_info=True)
                await asyncio.sleep(5)


@traced("telegram.send_briefing")
async def send_briefing(settings: Settings, store: Store) -> str:
    """Send today's saved briefing once; an ambiguous send requires manual review."""
    if len(settings.telegram_allowed_user_ids) != 1:
        raise ValueError(
            "Configure exactly one TELEGRAM_ALLOWED_USER_IDS for scheduled delivery"
        )
    day: date = datetime.now(KST).date()
    if not store.get("settings", "notifications", {"enabled": True})["enabled"]:
        feature_event("telegram.delivery", "skipped", reason="scheduled notifications disabled")
        return "skipped: scheduled notifications disabled"
    record = store.briefing(day)
    if not record:
        feature_event("telegram.delivery", "skipped", reason="no briefing saved for today")
        return "skipped: no briefing saved for today"
    user_id = next(iter(settings.telegram_allowed_user_ids))
    chat_id = store.telegram_chat(user_id)
    if not chat_id:
        feature_event("telegram.delivery", "skipped", reason="authorized user has not started the bot")
        return "skipped: authorized user has not started the bot"
    if not store.reserve_delivery(day):
        feature_event("telegram.delivery", "skipped", reason="delivery is already recorded or awaiting review")
        return "skipped: delivery is already recorded or awaiting review"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            api = TelegramAPI(settings.telegram_bot_token, client)
            view = daily_view(store, day)
            if view and view["body"] == record[1]:
                commands, labels = options(view)
                await api.send_text(chat_id, summary(view), choice_markup({
                    "id": view["id"], "options": commands, "labels": labels}))
            else:
                # Backward compatibility for briefings generated before snapshots existed.
                await api.send_text(chat_id, f"[국내주식 아침 브리핑: {day.isoformat()}]\n{record[1]}")
    except Exception:
        # Do not retry blindly after an uncertain API outcome: that can duplicate a briefing.
        store.finish_delivery(
            day, "review_required", "Telegram send failed or outcome unknown"
        )
        raise RuntimeError("Telegram send failed; delivery requires review") from None
    store.finish_delivery(day, "sent")
    return "sent"


@traced("telegram.send_test")
async def send_test(settings: Settings, store: Store) -> None:
    if len(settings.telegram_allowed_user_ids) != 1:
        raise ValueError("Configure exactly one TELEGRAM_ALLOWED_USER_IDS")
    chat_id = store.telegram_chat(next(iter(settings.telegram_allowed_user_ids)))
    if not chat_id:
        raise ValueError("Send /start from the allowed Telegram user first")
    async with httpx.AsyncClient(timeout=15) as client:
        await TelegramAPI(settings.telegram_bot_token, client).send_text(
            chat_id, "stock-briefing 텔레그램 연결 테스트"
        )


async def inspect_user_ids(settings: Settings) -> list[str]:
    """Bootstrap helper: run before the poller starts, after sending /start to the bot."""
    async with httpx.AsyncClient(timeout=15) as client:
        updates = await TelegramAPI(settings.telegram_bot_token, client).call(
            "getUpdates", {"timeout": 0, "allowed_updates": ["message"]}
        )
    return sorted(
        {
            str(msg["from"]["id"])
            for update in updates
            if (msg := update.get("message"))
            and msg.get("chat", {}).get("type") == "private"
            and msg.get("from", {}).get("id") is not None
        }
    )
