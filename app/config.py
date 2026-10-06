from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _ids(value: str) -> frozenset[str]:
    return frozenset(part.strip() for part in value.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    db_path: Path
    kakao_skill_secret: str
    kakao_allowed_user_ids: frozenset[str]
    telegram_bot_token: str
    telegram_allowed_user_ids: frozenset[str]

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            db_path=Path(os.getenv("SQLITE_PATH", "./stock-briefing.db")),
            kakao_skill_secret=os.getenv("KAKAO_SKILL_SECRET", ""),
            kakao_allowed_user_ids=_ids(os.getenv("KAKAO_ALLOWED_USER_IDS", "")),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_allowed_user_ids=_ids(os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")),
        )
