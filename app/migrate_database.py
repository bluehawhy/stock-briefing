"""Offline, transactional SQLite -> PostgreSQL import; never sends messages."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from app.config import Settings
from app.storage import Store

TABLES = ('telegram_recipients', 'poll_state', 'briefings', 'delivery_runs',
          'records', 'daily_bars', 'investor_flows', 'confirmations', 'jobs',
          'leases', 'application_logs', 'audit')
SERIAL_TABLES = ('jobs', 'application_logs', 'audit')


def migrate(source_path):
    source_path = source_path.resolve(strict=True)
    store = Store(Settings.from_env().db_path, allow_unmigrated=True)
    if store.database_options is None:
        raise RuntimeError('Select PostgreSQL explicitly before importing')
    store.init()
    # Read-only source, including WAL visibility. Stop all writers before import.
    with sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True) as source:
        source.execute('BEGIN')
        if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('SQLite integrity check failed')
        present = {r[0] for r in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )}
        if present != set(TABLES):
            raise RuntimeError('Source schema differs from supported tables; import stopped')
        counts = {}
        with store.connect() as target:
            target.execute('BEGIN IMMEDIATE')
            if target.execute('SELECT 1 FROM database_metadata LIMIT 1').fetchone():
                raise RuntimeError('Destination was already initialized; refusing overwrite')
            for table in TABLES:
                if target.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
                    raise RuntimeError('Destination must be empty; refusing overwrite')
            for table in TABLES:
                columns = [r[1] for r in source.execute(f'PRAGMA table_info({table})')]
                expected = {tuple(r) for r in source.execute(f'SELECT * FROM {table}')}
                # Old process ownership must not block the new API scheduler.
                if table == 'leases':
                    expected = set()
                if table == 'jobs':
                    expected = {tuple('failed' if c == 'status' and r[columns.index('status')] == 'running'
                                      else 'interrupted by database migration; request refresh again'
                                      if c == 'detail' and r[columns.index('status')] == 'running'
                                      else r[i] for i, c in enumerate(columns)) for r in expected}
                if expected:
                    placeholders = ','.join('?' for _ in columns)
                    target.executemany(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({placeholders})', expected)
                actual = {tuple(r) for r in target.execute(f'SELECT * FROM {table}').fetchall()}
                if actual != expected:
                    raise RuntimeError('Imported rows differ; transaction rolled back')
                counts[table] = len(actual)
            for table in SERIAL_TABLES:
                target.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                               f"COALESCE((SELECT MAX(id) FROM {table}), 1), "
                               f"EXISTS(SELECT 1 FROM {table}))")
            target.execute('INSERT INTO database_metadata VALUES (?,?)',
                           ('sqlite_import_complete', json.dumps({
                               'at': datetime.now(timezone.utc).isoformat(), 'counts': counts})))
    # No database contents, credentials, account numbers or source paths in output.
    print(json.dumps({'status': 'imported_and_verified', 'counts': counts}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sqlite', type=Path, required=True)
    args = parser.parse_args()
    try:
        migrate(args.sqlite)
    except Exception as exc:
        # Driver errors can contain connection details or row values.
        raise SystemExit('Import failed (' + type(exc).__name__ + '); destination transaction rolled back') from None


if __name__ == '__main__':
    main()
