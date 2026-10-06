from __future__ import annotations

from hmac import compare_digest

from fastapi import APIRouter, HTTPException, Request

from app.config import Settings
from app.services import BotService, InboundMessage
from app.storage import Store

router = APIRouter()


def skill_response(text: str) -> dict:
    # Kakao's simpleText has a 1,000 character limit; keep a visible truncation notice.
    if len(text) > 1000:
        text = text[:980] + "\n… 나머지는 별도 조회하세요."
    return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": text}}]}}


@router.post("/kakao/skill/{secret}")
async def kakao_skill(secret: str, request: Request) -> dict:
    settings = Settings.from_env()
    if not settings.kakao_skill_secret or not compare_digest(secret, settings.kakao_skill_secret):
        raise HTTPException(status_code=404)
    try:
        data = await request.json()
        user_request = data["userRequest"]
        user_id = user_request["user"]["id"]
        utterance = user_request["utterance"]
        if not isinstance(user_id, str) or not isinstance(utterance, str):
            raise ValueError("invalid fields")
    except (KeyError, TypeError, ValueError):
        return skill_response("메시지를 읽지 못했습니다. 다시 시도해 주세요.")
    answer = BotService(settings, Store(settings.db_path)).handle(
        InboundMessage(channel="kakao", user_id=user_id, text=utterance)
    )
    return skill_response(answer or "사용 권한이 없습니다.")
