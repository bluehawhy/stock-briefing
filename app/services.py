from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import Settings
from app.storage import Store

KST = ZoneInfo("Asia/Seoul")
HELP = (
    "사용 가능한 명령: 도움말, 오늘 브리핑, 서비스 상태, 연동 상태\n"
    "계좌·관심종목·매매 제안/주문 기능은 다음 구현 단계에서 연결됩니다."
)


@dataclass(frozen=True)
class InboundMessage:
    channel: str
    user_id: str
    text: str
    chat_id: str | None = None
    is_private: bool = True


class BotService:
    def __init__(self, settings: Settings, store: Store):
        self.settings, self.store = settings, store

    def handle(self, message: InboundMessage) -> str | None:
        allowed = (
            self.settings.kakao_allowed_user_ids
            if message.channel == "kakao"
            else self.settings.telegram_allowed_user_ids
            if message.channel == "telegram"
            else frozenset()
        )
        if not message.user_id or message.user_id not in allowed or not message.is_private:
            return None
        if message.channel == "telegram" and message.chat_id:
            self.store.register_telegram(message.user_id, message.chat_id)

        command = message.text.strip()
        if command in {"/start", "/help", "도움말"}:
            return HELP
        if command in {"오늘 브리핑", "브리핑", "/briefing"}:
            record = self.store.briefing()
            if not record:
                return "아직 저장된 브리핑이 없습니다. 데이터 수집·전략 엔진 연결 전입니다."
            day, body = record
            return f"[저장된 브리핑: {day}]\n{body}"
        if command in {"서비스 상태", "/status"}:
            record = self.store.briefing()
            delivery = self.store.last_delivery()
            return (
                f"최근 브리핑: {record[0] if record else '없음'}\n"
                f"텔레그램 자동 발송: {delivery[0] + ' / ' + delivery[1] if delivery else '이력 없음'}\n"
                f"확인 시각: {datetime.now(KST):%Y-%m-%d %H:%M} KST"
            )
        if command == "연동 상태":
            return "증권사·계좌 연동 기능은 아직 구현 전입니다. 현재 메시지 채널만 연결됩니다."
        return "지원하지 않는 명령입니다. '도움말'을 입력해 사용 가능한 명령을 확인하세요."
