from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from app.storage import Store


def test_live_backup_and_migration_preserve_original_records(store, tmp_path):
    store.save_briefing(date(2026, 10, 7), "original transport record")
    store.register_telegram("42", "42")
    backup = tmp_path / "snapshot.db.backup"
    store.backup(backup)
    assert Store(backup).briefing()[1] == "original transport record"
    assert Store(backup).telegram_chat("42") == "42"
    assert backup.stat().st_mode & 0o077 == 0
    assert store.path.stat().st_mode & 0o077 == 0
    with pytest.raises(ValueError):
        store.backup(backup)


def test_concurrent_delivery_reservation_has_one_winner(store):
    def reserve(_):
        return Store(store.path).reserve_delivery(date(2026, 10, 7))

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(reserve, range(8))) == 1


def test_concurrent_jobs_are_claimed_once(store):
    store.enqueue_refresh()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: Store(store.path).claim_job(), range(4)))
    assert results.count(1) == 1
    assert results.count(None) == 3
