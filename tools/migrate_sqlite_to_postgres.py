"""Copy application data from SQLite to an empty PostgreSQL database.

The source is opened read-only. The target must have an empty application
schema. This tool never truncates or deletes target data.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


TABLE_ORDER = (
    "organizacoes",
    "schema_migrations",
    "dca_modules",
    "dca_plans",
    "dca_plan_modules",
    "dca_organization_profiles",
    "dca_organization_specialties",
    "dv_unidades",
    "usuarios",
    "dca_memberships",
    "dca_organization_modules",
    "dca_super_admin_mfa",
    "dca_super_admin_recovery_codes",
    "pacientes",
    "passkey_credentials",
    "passkey_invites",
    "passkey_challenges",
    "agenda",
    "prontuario_soap",
    "odontograma",
    "arquivos",
    "document_versions",
    "dv_medicamentos",
    "dv_farmacia_lotes",
    "dv_farmacia_movimentacoes",
    "audit_events",
    "dca_subscriptions",
    "dca_subscription_events",
    "dca_payments",
    "dca_commercial_audit",
)


def _source_columns(source: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in source.execute(f'PRAGMA table_info("{table}")')]


def _target_columns(target, table: str) -> list[str]:
    rows = target.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema=current_schema() AND table_name=%s
           ORDER BY ordinal_position""",
        (table,),
    ).fetchall()
    return [row[0] for row in rows]


def _assert_empty_target(target) -> None:
    for table in TABLE_ORDER:
        target.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name=%s",
            (table,),
        )
        if not target.fetchone():
            continue
        if table == "schema_migrations":
            target.execute("SELECT version FROM schema_migrations WHERE version > 1 LIMIT 1")
            if target.fetchone():
                raise RuntimeError("Destino ja possui migracoes posteriores. Nenhum dado foi importado.")
            continue
        target.execute(f'SELECT EXISTS (SELECT 1 FROM "{table}" LIMIT 1)')
        if target.fetchone()[0]:
            raise RuntimeError(
                f"Destino nao vazio: tabela {table}. Nenhuma linha foi importada."
            )


def migrate(source_path: Path, apply: bool = False) -> dict[str, int]:
    if not source_path.is_file():
        raise FileNotFoundError("Arquivo SQLite de origem nao encontrado.")
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("Configure DATABASE_URL para PostgreSQL antes de executar.")

    with sqlite3.connect(f"file:{source_path.resolve().as_posix()}?mode=ro", uri=True) as source:
        source.row_factory = sqlite3.Row
        integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError("A verificacao de integridade da origem SQLite falhou.")
        foreign_key_errors = source.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise RuntimeError("A origem contem referencias estrangeiras inconsistentes.")

        # Prevent bootstrap credentials from creating an unexpected target user.
        for name in ("ADMIN_SENHA", "ADMIN_PIN"):
            os.environ.pop(name, None)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import database  # noqa: PLC0415

        database.criar_tabelas()
        import dv_saude  # noqa: PLC0415

        dv_saude._ensure_schema()
        import commercial  # noqa: PLC0415

        # Create empty target tables first so seeded catalog rows do not make
        # the safety check mistake application defaults for customer data.
        commercial.ensure_schema(seed_catalog=False)
        raw_target = database._get_pg_pool().getconn()
        counts: dict[str, int] = {}
        try:
            target = raw_target.cursor()
            _assert_empty_target(target)
            if not apply:
                for table in TABLE_ORDER:
                    exists = source.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,),
                    ).fetchone()
                    if exists:
                        counts[table] = source.execute(
                            f'SELECT COUNT(*) FROM "{table}"'
                        ).fetchone()[0]
                raw_target.rollback()
                return counts

            for table in TABLE_ORDER:
                if table == "schema_migrations":
                    # Schema version belongs to the destination installation.
                    continue
                exists = source.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if not exists:
                    continue
                source_columns = _source_columns(source, table)
                target_columns = _target_columns(target, table)
                columns = [column for column in source_columns if column in target_columns]
                if not columns:
                    continue
                quoted_columns = ", ".join(f'"{column}"' for column in columns)
                placeholders = ", ".join(["%s"] * len(columns))
                insert_sql = f'INSERT INTO "{table}" ({quoted_columns}) VALUES ({placeholders})'
                rows = source.execute(f'SELECT {quoted_columns} FROM "{table}"')
                batch = [tuple(row[column] for column in columns) for row in rows]
                if batch:
                    target.executemany(insert_sql, batch)
                counts[table] = len(batch)
                if table in {"organizacoes", "usuarios", "pacientes", "dv_unidades", "agenda", "prontuario_soap", "odontograma", "arquivos", "document_versions", "audit_events", "dv_medicamentos", "dv_farmacia_lotes", "dv_farmacia_movimentacoes", "passkey_invites", "dca_plans", "dca_organization_profiles", "dca_organization_modules", "dca_subscriptions", "dca_subscription_events", "dca_payments", "dca_commercial_audit"}:
                    target.execute(
                        "SELECT setval(pg_get_serial_sequence(%s, 'id'), COALESCE((SELECT MAX(id) FROM \"" + table + "\"), 1), EXISTS(SELECT 1 FROM \"" + table + "\"))",
                        (table,),
                    )
            raw_target.commit()
            database.criar_tabelas()
            dv_saude._ensure_schema()
            commercial.ensure_schema()
            return counts
        except Exception:
            raw_target.rollback()
            raise
        finally:
            database._get_pg_pool().putconn(raw_target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="caminho para o arquivo SQLite")
    parser.add_argument(
        "--apply", action="store_true",
        help="confirmar a cópia para um schema PostgreSQL vazio",
    )
    args = parser.parse_args()
    counts = migrate(args.source, apply=args.apply)
    mode = "importadas" if args.apply else "previstas (somente leitura)"
    print(f"Linhas {mode} por tabela:")
    for table, count in counts.items():
        print(f"{table}: {count}")
    if not args.apply:
        print("Nenhum registro foi importado. Revise o backup e execute novamente com --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
