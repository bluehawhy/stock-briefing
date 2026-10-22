"""PostgreSQL connection adapter for the store's fixed, parameterized SQL."""
from contextlib import contextmanager
import os
from pathlib import Path
import re


def database_options():
    password_file = os.getenv('POSTGRES_PASSWORD_FILE', '')
    backend = os.getenv('STOCK_DATABASE_BACKEND', 'sqlite')
    if backend == 'sqlite':
        return None
    if backend != 'postgres':
        raise RuntimeError('Unsupported STOCK_DATABASE_BACKEND')
    if not password_file:
        raise RuntimeError('POSTGRES_PASSWORD_FILE is required')
    password = Path(password_file).read_text().strip()
    if not password:
        raise RuntimeError('Database password is empty')
    return dict(host=os.getenv('POSTGRES_HOST', 'db'),
                port=int(os.getenv('POSTGRES_PORT', '5432')),
                dbname=os.getenv('POSTGRES_DB', 'stock_briefing'),
                user=os.getenv('POSTGRES_USER', 'stock_briefing'),
                password=password, connect_timeout=10,
                options='-c statement_timeout=30000 -c lock_timeout=10000')


class PostgresConnection:
    def __init__(self, connection):
        self.connection = connection

    @staticmethod
    def sql(statement):
        # All statements originate in application source, never in user input.
        ignore = bool(re.search(r'INSERT\s+OR\s+IGNORE', statement, re.I))
        statement = re.sub(r'INSERT\s+OR\s+IGNORE', 'INSERT', statement, flags=re.I)
        if ignore:
            statement = statement.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
        return statement.replace('?', '%s')

    def execute(self, statement, parameters=()):
        if statement.strip().upper() == 'BEGIN IMMEDIATE':
            # Preserve SQLite's serialized read/check/write operations, including
            # confirmation consumption, queue claims and collection reservations.
            return self.connection.execute('SELECT pg_advisory_xact_lock(734901, 1)')
        return self.connection.execute(self.sql(statement), parameters)

    def executemany(self, statement, rows):
        cursor = self.connection.cursor()
        cursor.executemany(self.sql(statement), rows)
        return cursor

    def executescript(self, script):
        self.connection.execute('SELECT pg_advisory_xact_lock(734901, 2)')
        script = script.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'BIGSERIAL PRIMARY KEY')
        script = re.sub(r'\bINTEGER\b', 'BIGINT', script)
        for statement in script.split(';'):
            if statement.strip():
                self.connection.execute(statement)


@contextmanager
def connect_postgres(options):
    import psycopg
    # No fallback to an empty local SQLite DB on PostgreSQL connection failure.
    with psycopg.connect(**options) as connection:
        yield PostgresConnection(connection)
