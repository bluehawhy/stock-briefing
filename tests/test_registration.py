from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.connections import digest, registration_link, valid_registration
from app.main import app
from app.providers import Vault
from app.registration import save_connection
from app.storage import Store


@pytest.fixture
def registration_settings(settings, tmp_path):
    key = tmp_path / "private" / "key"
    key.parent.mkdir()
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    return replace(
        settings,
        db_path=tmp_path / "data" / "stock-briefing.db",
        master_key_file=str(key),
        public_base_url="https://example.com",
    )


def test_secure_registration_single_use_encryption_and_no_balance_read(
    registration_settings, monkeypatch
):
    settings = registration_settings
    store = Store(settings.db_path)
    token = registration_link(settings, store).rsplit("/", 1)[1]
    calls = []
    monkeypatch.setattr(
        "app.registration.KisProvider.__init__", lambda self, *args, **kwargs: None
    )
    monkeypatch.setattr(
        "app.registration.KisProvider.authenticate", lambda self: calls.append("auth")
    )
    monkeypatch.setattr("app.registration.KisProvider.close", lambda self: None)
    monkeypatch.setattr(
        "app.registration.KisProvider.holdings",
        lambda *args: pytest.fail("unselected account queried"),
    )
    values = {
        "environment": "live",
        "app_key": "PRIVATE-KEY",
        "app_secret": "PRIVATE-SECRET",
        "number": "12345678",
        "product": "01",
    }
    account_id = save_connection(settings, store, token, values)
    assert calls == ["auth"]
    assert not store.get("accounts", account_id)["tracked"]
    assert "PRIVATE-SECRET" not in str(store.get("credentials", "kis"))
    assert (
        Vault(settings).open(store.get("credentials", "kis"))["app_secret"]
        == "PRIVATE-SECRET"
    )
    assert not valid_registration(store, token)
    with pytest.raises(ValueError, match="만료"):
        save_connection(settings, store, token, values)


def test_registration_http_security_and_expiry(registration_settings, monkeypatch):
    settings = registration_settings
    monkeypatch.setenv("SQLITE_PATH", str(settings.db_path))
    monkeypatch.setenv("MASTER_KEY_FILE", settings.master_key_file)
    monkeypatch.setenv("PUBLIC_BASE_URL", settings.public_base_url)
    store = Store(settings.db_path)
    token = registration_link(settings, store).rsplit("/", 1)[1]
    path = "/broker/register/" + token
    client = TestClient(app)
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert (
        client.post(
            path, data={}, headers={"origin": "https://attacker.example"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            path, json={}, headers={"origin": "https://example.com"}
        ).status_code
        == 415
    )
    store.put(
        "registration",
        digest(token),
        {
            "used": False,
            "expires_at": (
                datetime.now(timezone.utc) - timedelta(minutes=1)
            ).isoformat(),
        },
    )
    assert client.get(path).status_code == 404


def test_reject_plain_http_and_key_inside_db_folder(registration_settings):
    settings = registration_settings
    store = Store(settings.db_path)
    with pytest.raises(ValueError, match="HTTPS"):
        registration_link(
            replace(settings, public_base_url="http://example.com"), store
        )
    with pytest.raises(ValueError, match="DB 폴더"):
        Vault(replace(settings, master_key_file=str(settings.db_path.parent / "key")))
