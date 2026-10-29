import asyncio
from dataclasses import replace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.database import database_options
from app.models import DataError
from app.providers import Vault


def test_platform_database_url_is_used(monkeypatch):
    monkeypatch.delenv('STOCK_DATABASE_BACKEND', raising=False)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:password@db/project')
    assert database_options()['conninfo'] == 'postgresql://user:password@db/project'


def test_existing_database_file_configuration_still_works(monkeypatch, tmp_path):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('STOCK_DATABASE_BACKEND', 'postgres')
    password = tmp_path / 'password'
    password.write_text('secret')
    monkeypatch.setenv('POSTGRES_PASSWORD_FILE', str(password))
    assert database_options()['password'] == 'secret'


def test_platform_master_key_decrypts_existing_data(settings, tmp_path):
    key = Fernet.generate_key()
    path = tmp_path / 'key'
    path.write_bytes(key)
    path.chmod(0o600)
    old = Vault(replace(settings, db_path=tmp_path / 'db' / 'db.sqlite', master_key_file=str(path)))
    new = Vault(replace(settings, master_key=key.decode()))
    assert new.open(old.seal({'credential': 'value'})) == {'credential': 'value'}
    with pytest.raises(DataError):
        Vault(replace(settings, master_key='invalid'))


def test_api_owns_and_stops_telegram_poller(monkeypatch, tmp_path):
    from app import main
    import app.telegram

    started, stopped = [], []

    async def fake_poll(settings, store):
        started.append(True)
        try:
            await asyncio.Event().wait()
        finally:
            stopped.append(True)

    monkeypatch.setenv('SQLITE_PATH', str(tmp_path / 'test.db'))
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('STOCK_DATABASE_BACKEND', 'sqlite')
    monkeypatch.setenv('TELEGRAM_POLLING_ENABLED', '1')
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'token')
    monkeypatch.setenv('TELEGRAM_ALLOWED_USER_IDS', '42')
    monkeypatch.setattr(app.telegram, 'poll', fake_poll)
    with TestClient(main.app) as client:
        assert client.get('/healthz').status_code == 200
        assert started == [True]
    assert stopped == [True]


def test_polling_requires_configuration(monkeypatch, tmp_path):
    from app.main import app
    monkeypatch.setenv('TELEGRAM_POLLING_ENABLED', '1')
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', '')
    monkeypatch.setenv('TELEGRAM_ALLOWED_USER_IDS', '42')
    with pytest.raises(RuntimeError, match='configuration is incomplete'):
        with TestClient(app):
            pass
