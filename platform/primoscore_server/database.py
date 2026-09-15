"""Explicit-path SQLite storage with transactional, checksummed migrations.

This is a trusted server-internal boundary, not a browser-facing SQL API.
No module-level connection, startup seed, production connection or import.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sqlite3

MIGRATIONS = Path(__file__).parent / 'migrations'


def now():
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        if 'dist' in self.path.parts:
            raise ValueError('Il database deve restare fuori dalla cartella pubblicata.')

    @contextmanager
    def transaction(self):
        # mode=rw fails if init has not explicitly created the database.
        connection = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=10000')
        try:
            # Also serializes authorization with suspension and the protected operation.
            connection.execute('BEGIN IMMEDIATE')
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            descriptor = None
        if descriptor is not None:
            os.close(descriptor)
        with self.transaction() as connection:
            tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if tables and 'schema_migrations' not in tables:
                raise ValueError('Archivio esistente non riconosciuto: inizializzazione annullata.')
            connection.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY,sha256 TEXT NOT NULL,applied_at TEXT NOT NULL)')
            migrations = sorted(MIGRATIONS.glob('*.sql'))
            applied = {r['version']: r['sha256'] for r in connection.execute('SELECT * FROM schema_migrations')}
            if set(applied) - {p.name for p in migrations}:
                raise ValueError('Il database richiede una versione più recente del servizio.')
            for migration in migrations:
                sql = migration.read_text(encoding='utf-8')
                digest = hashlib.sha256(sql.encode()).hexdigest()
                if migration.name in applied:
                    if applied[migration.name] != digest:
                        raise ValueError('Una migrazione già applicata è stata modificata.')
                    continue
                # executescript commits implicitly: use complete_statement instead to keep
                # all DDL, triggers and the version marker in the same transaction.
                statement = ''
                for line in sql.splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        connection.execute(statement)
                        statement = ''
                if statement.strip():
                    raise ValueError('Migrazione SQL incompleta.')
                connection.execute('INSERT INTO schema_migrations VALUES(?,?,?)', (migration.name, digest, now()))
            if connection.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('Relazioni del database non valide.')
