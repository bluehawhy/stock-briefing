from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from app.config import Settings
from app.models import DataError
from app.providers import Vault
from app.storage import Store


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def registration_link(settings: Settings, store: Store) -> str:
    url = urlparse(settings.public_base_url)
    if (
        url.scheme != "https"
        or not url.netloc
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise DataError("PUBLIC_BASE_URL에 등록 화면의 HTTPS 주소를 설정하세요.")
    Vault(settings)
    token = secrets.token_urlsafe(32)
    store.put(
        "registration",
        digest(token),
        {
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=5)
            ).isoformat(),
            "used": False,
        },
    )
    return settings.public_base_url + "/broker/register/" + token


def valid_registration(store: Store, token: str) -> bool:
    if not 30 <= len(token) <= 100:
        return False
    record = store.get("registration", digest(token))
    return bool(
        record
        and not record["used"]
        and record["expires_at"] > datetime.now(timezone.utc).isoformat()
    )
