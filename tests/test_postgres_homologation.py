"""Opt-in PostgreSQL checks for the isolated synthetic homologation service."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import pytest

import database


pytestmark = pytest.mark.postgres


def test_postgres_connection_schema_indexes_constraints_and_synthetic_rows(monkeypatch):
    dsn = os.environ.get("DV_TEST_POSTGRES_URL", "").strip()
    if not dsn:
        pytest.skip("Set DV_TEST_POSTGRES_URL only for the isolated Docker homologation DB.")
    parsed = urlsplit(dsn)
    if parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.port != 55439:
        pytest.fail("PostgreSQL integration tests accept only loopback port 55439.")
    if parsed.path.lstrip("/") != "dca_homologation":
        pytest.fail("PostgreSQL integration tests accept only database dca_homologation.")

    if database._pg_pool is not None:
        pytest.fail("Close any existing PostgreSQL pool before running isolated integration tests.")
    monkeypatch.setattr(database, "_DATABASE_URL", dsn)
    monkeypatch.setattr(database, "_USE_POSTGRES", True)
    conn = database.conectar()
    try:
        assert conn.execute("SELECT 1 AS connected").fetchone()["connected"] == 1
        expected_tables = {
            "organizacoes", "usuarios", "pacientes", "arquivos", "document_versions",
            "dca_modules", "dca_plans", "dca_subscriptions", "dca_super_admin_mfa",
            "dca_super_admin_recovery_codes",
        }
        tables = {
            row["table_name"] for row in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()"
            ).fetchall()
        }
        assert expected_tables.issubset(tables)
        patient_count = conn.execute(
            "SELECT COUNT(*) AS n FROM pacientes WHERE nome=?",
            ("Paciente Sintetico Sem Dados Reais",),
        ).fetchone()["n"]
        assert patient_count == 1
        orphan_docs = conn.execute(
            "SELECT COUNT(*) AS n FROM arquivos a LEFT JOIN pacientes p ON p.id=a.paciente_id WHERE p.id IS NULL"
        ).fetchone()["n"]
        assert orphan_docs == 0
        indexes = {
            row["indexname"] for row in conn.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname=current_schema()"
            ).fetchall()
        }
        assert "idx_pacientes_cnes_unidade" in indexes
        unvalidated = conn.execute(
            "SELECT COUNT(*) AS n FROM pg_constraint WHERE connamespace=current_schema()::regnamespace AND NOT convalidated"
        ).fetchone()["n"]
        assert unvalidated == 0
        conn.commit()
    finally:
        conn.close()
        database.close_postgres_pool()
        monkeypatch.setattr(database, "_pg_pool", None)
