from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings
from app.services import BotService, InboundMessage
from app.storage import Store

LOG = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


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
        if response.status_code >= 400:
            raise RuntimeError(f"Telegram {method} HTTP {response.status_code}")
        result = response.json()
        if not result.get("ok"):
            # Do not print the request URL: it contains the bot token.
            raise RuntimeError(f"Telegram {method} failed: {result.get('error_code', 'unknown')}")
        return result["result"]

    async def send_text(self, chat_id: str, text: str) -> None:
        # Keep chunks under Telegram's 4096-character sendMessage limit.
        while text:
            chunk, text = text[:4000], text[4000:]
            await self.call("sendMessage", {"chat_id": chat_id, "text": chunk})


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
                        "allowed_updates": ["message"],
                    },
                )
                for update in updates:
                    update_id = update["update_id"]
                    message = update.get("message") or {}
                    user = message.get("from") or {}
                    chat = message.get("chat") or {}
                    if isinstance(message.get("text"), str):
                        user_id = str(user.get("id", ""))
                        chat_id = str(chat.get("id", ""))
                        reply = service.handle(
                            InboundMessage(
                                channel="telegram",
                                user_id=user_id,
                                chat_id=chat_id,
                                text=message["text"],
                                is_private=chat.get("type") == "private",
                            )
                        )
                        if reply and chat_id:
                            await api.send_text(chat_id, reply)
                    # Advance only after processing; a crash can replay a read-only command.
                    store.set_poll_offset(update_id + 1)
            except (httpx.HTTPError, RuntimeError, KeyError, ValueError) as exc:
                # httpx exceptions may include a URL with a bot token; log only the type.
                LOG.warning("Telegram poll failed (%s); retrying", type(exc).__name__)
                await asyncio.sleep(5)


async def send_briefing(settings: Settings, store: Store) -> str:
    """Send today's saved briefing once; an ambiguous send requires manual review."""
    if len(settings.telegram_allowed_user_ids) != 1:
        raise ValueError("Configure exactly one TELEGRAM_ALLOWED_USER_IDS for scheduled delivery")
    day: date = datetime.now(KST).date()
    record = store.briefing(day)
    if not record:
        return "skipped: no briefing saved for today"
    user_id = next(iter(settings.telegram_allowed_user_ids))
    chat_id = store.telegram_chat(user_id)
    if not chat_id:
        return "skipped: authorized user has not started the bot"
    if not store.reserve_delivery(day):
        return "skipped: delivery is already recorded or awaiting review"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await TelegramAPI(settings.telegram_bot_token, client).send_text(
                chat_id, f"[국내주식 아침 브리핑: {day.isoformat()}]\n{record[1]}"
            )
    except Exception:
        # Do not retry blindly after an uncertain API outcome: that can duplicate a briefing.
        store.finish_delivery(day, "review_required", "Telegram send failed or outcome unknown")
        raise RuntimeError("Telegram send failed; delivery requires review") from None
    store.finish_delivery(day, "sent")
    return "sent"


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
