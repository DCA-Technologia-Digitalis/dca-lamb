from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

import database


def test_sqlite_ddl_translation_handles_postgres_types_and_transactions():
    ddl = database._translate_pg_sql(
        """CREATE TABLE sample (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payload BLOB,
            seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    assert "SERIAL PRIMARY KEY" in ddl
    assert "BYTEA" in ddl
    assert "seen_at TIMESTAMP" in ddl
    assert "created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text)" in ddl
    assert database._translate_pg_sql("BEGIN IMMEDIATE") == "BEGIN"


class _Cursor:
    def __init__(self):
        self.rowcount = 1
        self.sql = None

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchone(self):
        return {"id": 42}

    def close(self):
        pass


class _Connection:
    def __init__(self):
        self._total_changes = 0

    @property
    def total_changes(self):
        return self._total_changes


def test_postgres_cursor_preserves_insert_ids_only_for_serial_tables():
    connection = _Connection()
    cursor = _Cursor()
    wrapper = database._PgCursorWrapper(cursor, connection)
    wrapper.execute("INSERT INTO pacientes (nome) VALUES (?)", ("Fictício",))
    assert "RETURNING id" in cursor.sql
    assert wrapper.lastrowid == 42
    assert connection.total_changes == 1

    cursor = _Cursor()
    wrapper = database._PgCursorWrapper(cursor, connection)
    wrapper.execute("INSERT INTO schema_migrations(version) VALUES (?)", (1,))
    assert "RETURNING id" not in cursor.sql
    assert wrapper.lastrowid is None

    cursor = _Cursor()
    wrapper = database._PgCursorWrapper(cursor, connection)
    wrapper.execute("INSERT INTO passkey_challenges(id) VALUES (?)", ("challenge",))
    assert "RETURNING id" not in cursor.sql

    for table in (
        "dca_modules", "dca_organization_profiles", "dca_plan_modules",
        "dca_organization_specialties", "dca_organization_modules", "dca_memberships",
        "dca_super_admin_mfa", "dca_super_admin_recovery_codes",
    ):
        cursor = _Cursor()
        wrapper = database._PgCursorWrapper(cursor, connection)
        wrapper.execute(f"INSERT INTO {table} (placeholder) VALUES (?)", ("value",))
        assert "RETURNING id" not in cursor.sql
