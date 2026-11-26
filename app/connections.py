from __future__ import annotations

from app.logs import traced, feature_event

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


@traced("connections.registration_link")
def registration_link(settings: Settings, store: Store, user_id: str | None = None) -> str:
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
    record = {
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        "used": False,
    }
    if user_id is not None:
        store.put_for_user("registration", digest(token), record, str(user_id))
    else:
        store.put("registration", digest(token), record)
    return settings.public_base_url + "/broker/register/" + token


def valid_registration(store: Store, token: str, user_id: str | None = None) -> bool:
    if not 30 <= len(token) <= 100:
        return False
    record = (store.get_for_user("registration", digest(token), str(user_id)) if user_id is not None else store.get("registration", digest(token)))
    return bool(
        record
        and not record["used"]
        and record["expires_at"] > datetime.now(timezone.utc).isoformat()
    )
