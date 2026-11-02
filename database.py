"""
database.py — Gerenciamento do Banco de Dados SQLite (DV Odonto & Saúde Multiprofissional)
"""

import json
import hashlib
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
import bcrypt

DB_NAME = os.environ.get(
    "DV_DATABASE_PATH",
    str(Path(__file__).resolve().with_name("dv_odonto.db")),
)
ALLOWED_USER_PROFILES = {
    "ADMIN",
    "RECEPCAO",
    "RECEPÇÃO",
    "DENTISTA",
    "MEDICO",
    "MÉDICO",
    "ENFERMEIRO",
    "ENFERMEIRA",
    "PSICOLOGO",
    "PSICÓLOGO",
    "FARMACIA",
    "FARMÁCIA",
    "FARMACEUTICO",
    "FARMACÊUTICO",
}


import re

_DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
_USE_POSTGRES = bool(_DATABASE_URL and _DATABASE_URL.lower().startswith("postgres"))
_DB_POOL_MIN = int(os.environ.get("DB_POOL_MIN", "1"))
_DB_POOL_MAX = int(os.environ.get("DB_POOL_MAX", "8"))
_pg_pool = None
_pg_pool_lock = threading.Lock()


def _get_pg_pool():
    global _pg_pool
    if _pg_pool is None:
        with _pg_pool_lock:
            if _pg_pool is None:
                try:
                    from psycopg2 import pool
                except ImportError as exc:
                    raise RuntimeError(
                        "DATABASE_URL aponta para PostgreSQL, mas psycopg2 nao esta instalado."
                    ) from exc
                _pg_pool = pool.ThreadedConnectionPool(
                    max(1, _DB_POOL_MIN), max(max(1, _DB_POOL_MIN), _DB_POOL_MAX),
                    dsn=_DATABASE_URL,
                )
    return _pg_pool


def close_postgres_pool():
    """Close this worker's PostgreSQL connections during a graceful shutdown."""
    global _pg_pool
    with _pg_pool_lock:
        if _pg_pool is not None:
            _pg_pool.closeall()
            _pg_pool = None


def _pg_integrity_error_types():
    types = [sqlite3.IntegrityError]
    try:
        import psycopg2

        types.append(psycopg2.IntegrityError)
    except ImportError:
        pass
    return tuple(types)


PG_INTEGRITY_ERRORS = _pg_integrity_error_types()


def _translate_pg_sql(sql):
    if not isinstance(sql, str):
        return sql
    stripped = sql.strip()
    upper = stripped.upper()

    if re.fullmatch(r"BEGIN\s+IMMEDIATE(?:\s+TRANSACTION)?;?", stripped, re.IGNORECASE):
        return "BEGIN"

    if upper == "PRAGMA INTEGRITY_CHECK":
        return "SELECT 'ok'"
    if upper.startswith("PRAGMA FOREIGN_KEYS"):
        return "SELECT 1"
    match = re.match(r"PRAGMA\s+TABLE_INFO\((.+)\)", stripped, re.IGNORECASE)
    if match:
        table = match.group(1).strip().strip("\"'")
        return (
            "SELECT column_name AS name FROM information_schema.columns "
            "WHERE table_name = %s"
        ), (table,)

    sql = re.sub(
        r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
        "SERIAL PRIMARY KEY",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(r"\bBLOB\b", "BYTEA", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bDATETIME\b", "TIMESTAMP", sql, flags=re.IGNORECASE)
    sql = re.sub(
        r"(?im)^(\s*[a-z_][a-z0-9_]*\s+TEXT\s+NOT NULL\s+DEFAULT\s+)CURRENT_TIMESTAMP\b",
        r"\1(CURRENT_TIMESTAMP::text)",
        sql,
    )

    translated = []
    in_single = False
    in_double = False
    for char in sql:
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        if char == "?" and not in_single and not in_double:
            translated.append("%s")
        else:
            translated.append(char)
    return "".join(translated)


class _PgCursorWrapper:
    def __init__(self, cursor, connection):
        self._cursor = cursor
        self._connection = connection
        self._lastrowid = None

    def execute(self, sql, params=None):
        translated = _translate_pg_sql(sql)
        extra_params = None
        if isinstance(translated, tuple):
            translated, extra_params = translated
            params = extra_params
        insert_match = re.match(
            r"\s*INSERT\s+INTO\s+([a-z_][a-z0-9_]*)\b", str(sql), re.IGNORECASE
        )
        table_name = insert_match.group(1).lower() if insert_match else ""
        needs_insert_id = bool(
            insert_match
            and table_name not in {
                "passkey_credentials", "passkey_challenges", "schema_migrations",
                "dca_modules", "dca_organization_profiles", "dca_plan_modules",
                "dca_organization_specialties",
                "dca_organization_modules", "dca_memberships",
                "dca_super_admin_mfa", "dca_super_admin_recovery_codes",
            }
            and "RETURNING" not in str(sql).upper()
        )
        if needs_insert_id:
            translated = f"{translated.rstrip().rstrip(';')} RETURNING id"
        result = self._cursor.execute(translated, params)
        if needs_insert_id:
            row = self._cursor.fetchone()
            self._lastrowid = row["id"] if row else None
        if self.rowcount > 0:
            self._connection._total_changes += self.rowcount
        return result

    def executemany(self, sql, params_seq):
        translated = _translate_pg_sql(sql)
        if isinstance(translated, tuple):
            translated = translated[0]
        result = self._cursor.executemany(translated, params_seq)
        if self.rowcount > 0:
            self._connection._total_changes += self.rowcount
        return result

    def executescript(self, sql):
        for statement in str(sql).split(";"):
            statement = statement.strip()
            if statement:
                self.execute(statement)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchmany(self, size=None):
        if size is None:
            return self._cursor.fetchmany()
        return self._cursor.fetchmany(size)

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def lastrowid(self):
        return self._lastrowid

    @property
    def description(self):
        return self._cursor.description

    def close(self):
        self._cursor.close()

    def __iter__(self):
        return iter(self._cursor)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _PgConnectionWrapper:
    def __init__(self, raw_connection):
        self._raw = raw_connection
        self.row_factory = None
        self._total_changes = 0

    @property
    def total_changes(self):
        return self._total_changes

    def cursor(self, *args, **kwargs):
        extras = __import__("psycopg2.extras", fromlist=["DictCursor"])
        raw_cursor = self._raw.cursor(cursor_factory=extras.DictCursor)
        return _PgCursorWrapper(raw_cursor, self)

    def execute(self, sql, params=None):
        cursor = self.cursor()
        cursor.execute(sql, params)
        return cursor

    def executescript(self, sql):
        cursor = self.cursor()
        cursor.executescript(sql)
        return cursor

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        try:
            if self._raw.get_transaction_status() != 0:
                self._raw.rollback()
            _get_pg_pool().putconn(self._raw)
        except Exception:
            try:
                self._raw.close()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self._raw.commit()
        else:
            self._raw.rollback()
        self.close()
        return False


def conectar():
    """Conecta ao banco de dados (SQLite local ou PostgreSQL em producao)."""
    if _USE_POSTGRES:
        return _PgConnectionWrapper(_get_pg_pool().getconn())

    conn = sqlite3.connect(DB_NAME, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def criar_tabelas():
    """Cria todas as tabelas necessárias no banco de dados caso não existam."""
    conn = conectar()
    c = conn.cursor()
    c.execute("BEGIN IMMEDIATE")
    if _USE_POSTGRES:
        c.execute("SELECT pg_advisory_xact_lock(731904261)")

    c.execute("""
    CREATE TABLE IF NOT EXISTS organizacoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        slug TEXT NOT NULL UNIQUE,
        criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # 1. Tabela de Pacientes (Padrão e-SUS / Cartão SUS)
    c.execute("""
    CREATE TABLE IF NOT EXISTS pacientes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        telefone TEXT,
        cpf TEXT,
        cns TEXT,                  -- Cartão Nacional de Saúde (SUS)
        cnes_unidade TEXT,         -- Código da Unidade de Saúde (CNES)
        data_nascimento TEXT,
        sexo TEXT,
        bairro TEXT,
        peso REAL,
        altura REAL,
        pressao TEXT,
        imc REAL,
        classificacao_imc TEXT
    )
    """)

    # 2. Tabela de Odontograma (com suporte a Código SIGTAP)
    c.execute("""
    CREATE TABLE IF NOT EXISTS odontograma(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paciente_id INTEGER,
        dente TEXT,
        face TEXT,
        condicao TEXT,
        codigo_sigtap TEXT,         -- Código do procedimento no SUS
        FOREIGN KEY (paciente_id) REFERENCES pacientes (id)
    )
    """)

    # 3. Tabela de Arquivos/Anexos
    c.execute("""
    CREATE TABLE IF NOT EXISTS arquivos(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paciente_id INTEGER,
        tipo TEXT,
        nome_original TEXT,
        nome_stored TEXT,
        mime_type TEXT,
        descricao TEXT,
        criado_em TEXT,
        FOREIGN KEY (paciente_id) REFERENCES pacientes (id)
    )
    """)

    # 4. Tabela da Agenda
    c.execute("""
    CREATE TABLE IF NOT EXISTS agenda(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paciente_id INTEGER,
        data_hora TEXT,
        especialidade TEXT,
        procedimento TEXT,
        FOREIGN KEY (paciente_id) REFERENCES pacientes (id)
    )
    """)

    # 5. Tabela de Evolução SOAP (e-SUS APS)
    c.execute("""
    CREATE TABLE IF NOT EXISTS prontuario_soap (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paciente_id INTEGER NOT NULL,
        data_atendimento DATETIME DEFAULT CURRENT_TIMESTAMP,
        subjetivo TEXT,
        objetivo TEXT,
        avaliacao TEXT,
        plano TEXT,
        FOREIGN KEY (paciente_id) REFERENCES pacientes (id)
    )
    """)

    # 6. Tabela de Usuários do Sistema (Controle de Acesso RBAC)
    c.execute("""
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        usuario TEXT UNIQUE NOT NULL,
        senha_hash TEXT NOT NULL DEFAULT '',
        perfil TEXT NOT NULL,
        registro_profissional TEXT,
        cnes_unidade TEXT,
        passkey_user_handle BLOB,
        session_version INTEGER NOT NULL DEFAULT 0,
        pin_hash TEXT,
        pin_tentativas INTEGER NOT NULL DEFAULT 0,
        pin_bloqueado_ate TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS dv_unidades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cnes TEXT NOT NULL UNIQUE,
        nome TEXT NOT NULL,
        organization_id INTEGER,
        criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS audit_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id INTEGER,
        actor_user_id INTEGER,
        action TEXT NOT NULL,
        resource_type TEXT NOT NULL,
        resource_id TEXT,
        request_id TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (organization_id) REFERENCES organizacoes(id),
        FOREIGN KEY (actor_user_id) REFERENCES usuarios(id)
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS document_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id INTEGER,
        arquivo_id INTEGER NOT NULL,
        version_number INTEGER NOT NULL,
        storage_key TEXT NOT NULL,
        sha256 TEXT,
        media_type TEXT,
        actor_user_id INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (arquivo_id, version_number),
        FOREIGN KEY (organization_id) REFERENCES organizacoes(id),
        FOREIGN KEY (arquivo_id) REFERENCES arquivos(id),
        FOREIGN KEY (actor_user_id) REFERENCES usuarios(id)
    )
    """)

    # Additive tenant columns keep old records intact. Legacy units are
    # provisionally isolated one CNES per organization until an administrator
    # explicitly groups units that belong to the same organization.
    tenant_tables = {
        "dv_unidades": "organization_id",
        "pacientes": "organization_id",
        "usuarios": "organization_id",
        "arquivos": "organization_id",
        "agenda": "organization_id",
        "prontuario_soap": "organization_id",
        "odontograma": "organization_id",
    }
    for table, column in tenant_tables.items():
        columns = {row["name"] for row in c.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} INTEGER")

    c.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_org_created ON audit_events(organization_id, created_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_document_versions_file ON document_versions(arquivo_id, version_number)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_document_versions_org ON document_versions(organization_id, created_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_pacientes_org_nome ON pacientes(organization_id, nome)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_usuarios_org ON usuarios(organization_id)")

    patient_columns = {
        row["name"] for row in c.execute("PRAGMA table_info(pacientes)")
    }
    if "cnes_unidade" not in patient_columns:
        c.execute("ALTER TABLE pacientes ADD COLUMN cnes_unidade TEXT")

    user_columns = {row["name"] for row in c.execute("PRAGMA table_info(usuarios)")}
    if "cnes_unidade" not in user_columns:
        c.execute("ALTER TABLE usuarios ADD COLUMN cnes_unidade TEXT")
    if "passkey_user_handle" not in user_columns:
        c.execute("ALTER TABLE usuarios ADD COLUMN passkey_user_handle BLOB")
    if "session_version" not in user_columns:
        c.execute(
            "ALTER TABLE usuarios ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0"
        )
    if "pin_hash" not in user_columns:
        c.execute("ALTER TABLE usuarios ADD COLUMN pin_hash TEXT")
    if "pin_tentativas" not in user_columns:
        c.execute("ALTER TABLE usuarios ADD COLUMN pin_tentativas INTEGER NOT NULL DEFAULT 0")
    if "pin_bloqueado_ate" not in user_columns:
        c.execute("ALTER TABLE usuarios ADD COLUMN pin_bloqueado_ate TEXT")
    for row in c.execute(
        "SELECT id FROM usuarios WHERE passkey_user_handle IS NULL"
    ).fetchall():
        c.execute(
            "UPDATE usuarios SET passkey_user_handle = ? WHERE id = ?",
            (secrets.token_bytes(32), row["id"]),
        )
    c.execute("""
    CREATE TABLE IF NOT EXISTS passkey_credentials (
        credential_id BLOB PRIMARY KEY,
        user_id INTEGER NOT NULL,
        credential_data BLOB NOT NULL,
        sign_count INTEGER NOT NULL DEFAULT 0,
        criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES usuarios (id) ON DELETE CASCADE
    )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_passkey_credentials_user_id "
        "ON passkey_credentials(user_id)"
    )
    c.execute("""
    CREATE TABLE IF NOT EXISTS passkey_invites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token_hash TEXT NOT NULL UNIQUE,
        user_id INTEGER NOT NULL,
        expires_at TEXT NOT NULL,
        used_at TEXT,
        criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES usuarios (id) ON DELETE CASCADE
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS passkey_challenges (
        id TEXT PRIMARY KEY,
        ceremony TEXT NOT NULL,
        state_json TEXT NOT NULL,
        user_id INTEGER,
        invite_hash TEXT,
        expires_at TEXT NOT NULL,
        used_at TEXT,
        FOREIGN KEY (user_id) REFERENCES usuarios (id) ON DELETE CASCADE
    )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_usuarios_cnes_unidade ON usuarios(cnes_unidade)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_pacientes_cnes_unidade ON pacientes(cnes_unidade)"
    )

    for unit in c.execute(
        "SELECT cnes, nome, organization_id FROM dv_unidades ORDER BY cnes"
    ).fetchall():
        organization_id = unit["organization_id"]
        if organization_id is None:
            slug = f"cnes-{unit['cnes']}"
            organization = c.execute(
                "SELECT id FROM organizacoes WHERE slug=?", (slug,)
            ).fetchone()
            if organization:
                organization_id = organization["id"]
            else:
                c.execute(
                    "INSERT INTO organizacoes(nome, slug) VALUES (?, ?)",
                    (unit["nome"], slug),
                )
                organization_id = c.lastrowid
            c.execute(
                "UPDATE dv_unidades SET organization_id=? WHERE cnes=?",
                (organization_id, unit["cnes"]),
            )
        for table in ("pacientes", "usuarios", "arquivos"):
            c.execute(
                f"UPDATE {table} SET organization_id=? WHERE cnes_unidade=? AND organization_id IS NULL",
                (organization_id, unit["cnes"]),
            )
        # Related clinical records inherit organization through their patient.
        for table in ("agenda", "prontuario_soap", "odontograma"):
            c.execute(
                f"UPDATE {table} SET organization_id=? WHERE organization_id IS NULL AND paciente_id IN (SELECT id FROM pacientes WHERE cnes_unidade=?)",
                (organization_id, unit["cnes"]),
            )
    c.execute(
        """INSERT INTO document_versions
           (organization_id, arquivo_id, version_number, storage_key, sha256, media_type, created_at)
           SELECT a.organization_id, a.id, 1, a.nome_stored, NULL, a.mime_type, a.criado_em
           FROM arquivos a
           WHERE NOT EXISTS (
             SELECT 1 FROM document_versions v WHERE v.arquivo_id=a.id
           )"""
    )
    c.execute("INSERT INTO schema_migrations(version) SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version=1)")

    conn.commit()
    conn.close()


def garantir_organizacao_unidade(cnes, nome):
    """Create the provisional one-CNES organization mapping for a new unit."""
    if not cnes or len(str(cnes)) != 7 or not str(cnes).isdigit():
        raise ValueError("CNES inválido para vínculo organizacional.")
    conn = conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        unit = conn.execute(
            "SELECT organization_id FROM dv_unidades WHERE cnes=?", (cnes,)
        ).fetchone()
        if not unit:
            raise ValueError("Unidade CNES não encontrada.")
        organization_id = unit["organization_id"]
        if organization_id is None:
            slug = f"cnes-{cnes}"
            organization = conn.execute(
                "SELECT id FROM organizacoes WHERE slug=?", (slug,)
            ).fetchone()
            if organization:
                organization_id = organization["id"]
            else:
                cursor = conn.execute(
                    "INSERT INTO organizacoes(nome, slug) VALUES (?, ?)",
                    (str(nome).strip()[:160] or slug, slug),
                )
                organization_id = cursor.lastrowid
            conn.execute(
                "UPDATE dv_unidades SET organization_id=? WHERE cnes=?",
                (organization_id, cnes),
            )
        for table in ("pacientes", "usuarios", "arquivos"):
            columns = {
                row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
            }
            if {"organization_id", "cnes_unidade"}.issubset(columns):
                conn.execute(
                    f"UPDATE {table} SET organization_id=? WHERE cnes_unidade=? AND organization_id IS NULL",
                    (organization_id, cnes),
                )
        for table in ("agenda", "prontuario_soap", "odontograma"):
            columns = {
                row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
            }
            if {"organization_id", "paciente_id"}.issubset(columns):
                conn.execute(
                    f"UPDATE {table} SET organization_id=? WHERE organization_id IS NULL AND paciente_id IN (SELECT id FROM pacientes WHERE cnes_unidade=?)",
                    (organization_id, cnes),
                )
        conn.commit()
        return organization_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def obter_organizacao_unidade(cnes):
    if not cnes:
        return None
    conn = conectar()
    try:
        row = conn.execute(
            "SELECT organization_id FROM dv_unidades WHERE cnes=?", (cnes,)
        ).fetchone()
        return row["organization_id"] if row else None
    finally:
        conn.close()


def listar_organizacoes():
    conn = conectar()
    try:
        rows = conn.execute(
            """SELECT o.id, o.nome, o.slug, COUNT(u.cnes) AS unidades
               FROM organizacoes o LEFT JOIN dv_unidades u ON u.organization_id=o.id
               GROUP BY o.id, o.nome, o.slug HAVING COUNT(u.cnes)>0 ORDER BY o.nome"""
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def criar_organizacao_e_vincular_unidade(
    nome, cnes, actor_user_id=None, request_id=None, organization_id=None
):
    """Create or select an organization and move a unit's records to it."""
    nome = str(nome or "").strip()
    cnes = str(cnes or "").strip()
    if len(nome) > 160 or len(cnes) != 7 or not cnes.isdigit():
        raise ValueError("Informe um nome de organização válido e um CNES válido.")
    if organization_id is None and not nome:
        raise ValueError("Informe o nome da organização.")
    slug_base = re.sub(r"[^a-z0-9]+", "-", nome.lower()).strip("-")[:80] or "organizacao"
    conn = conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        unit = conn.execute("SELECT cnes FROM dv_unidades WHERE cnes=?", (cnes,)).fetchone()
        if not unit:
            raise ValueError("Unidade CNES não encontrada.")
        if organization_id is None:
            slug = slug_base
            suffix = 2
            while conn.execute("SELECT 1 FROM organizacoes WHERE slug=?", (slug,)).fetchone():
                slug = f"{slug_base[:72]}-{suffix}"
                suffix += 1
            organization_id = conn.execute(
                "INSERT INTO organizacoes(nome, slug) VALUES (?, ?)", (nome, slug)
            ).lastrowid
            audit_action = "admin_organization_create"
        else:
            try:
                organization_id = int(organization_id)
            except (TypeError, ValueError) as exc:
                raise ValueError("Organização selecionada inválida.") from exc
            if not conn.execute("SELECT 1 FROM organizacoes WHERE id=?", (organization_id,)).fetchone():
                raise ValueError("Organização selecionada não encontrada.")
            audit_action = "admin_unit_assign"
        conn.execute(
            "UPDATE dv_unidades SET organization_id=? WHERE cnes=?", (organization_id, cnes)
        )
        for table in ("pacientes", "usuarios", "dv_farmacia_lotes", "dv_farmacia_movimentacoes"):
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if {"organization_id", "cnes_unidade"}.issubset(columns):
                conn.execute(f"UPDATE {table} SET organization_id=? WHERE cnes_unidade=?", (organization_id, cnes))
        conn.execute(
            """UPDATE arquivos SET organization_id=? WHERE paciente_id IN
               (SELECT id FROM pacientes WHERE cnes_unidade=?)""",
            (organization_id, cnes),
        )
        for table in ("agenda", "prontuario_soap", "odontograma"):
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if {"organization_id", "paciente_id"}.issubset(columns):
                conn.execute(
                    f"UPDATE {table} SET organization_id=? WHERE paciente_id IN (SELECT id FROM pacientes WHERE cnes_unidade=?)",
                    (organization_id, cnes),
                )
        conn.execute(
            """UPDATE document_versions SET organization_id=? WHERE arquivo_id IN (
                   SELECT a.id FROM arquivos a JOIN pacientes p ON p.id=a.paciente_id
                   WHERE p.cnes_unidade=?)""",
            (organization_id, cnes),
        )
        if actor_user_id is not None:
            conn.execute(
                """INSERT INTO audit_events(organization_id, actor_user_id, action, resource_type, resource_id, request_id)
                   VALUES (?, ?, ?, 'organization', ?, ?)""",
                (organization_id, actor_user_id, audit_action, str(organization_id), request_id),
            )
        conn.commit()
        return organization_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def registrar_auditoria(
    actor_user_id, action, resource_type, resource_id=None, request_id=None
):
    """Record a minimal audit event. Never accept patient data as metadata."""
    allowed_actions = {
        "login_success", "login_failure", "dca_mfa_login_success", "patient_list", "patient_view",
        "patient_create", "patient_update", "document_upload", "document_download",
        "document_create", "document_version", "appointment_delete", "odontogram_update",
        "soap_create", "pharmacy_movement", "admin_user_create", "admin_unit_create",
    }
    allowed_resources = {
        "auth", "patient", "document", "appointment", "odontogram", "soap",
        "pharmacy_movement", "user", "unit",
    }
    if action not in allowed_actions or resource_type not in allowed_resources:
        raise ValueError("Tipo de evento de auditoria não permitido.")
    organization_id = None
    conn = conectar()
    try:
        if actor_user_id is not None:
            actor = conn.execute(
                "SELECT organization_id FROM usuarios WHERE id=?", (actor_user_id,)
            ).fetchone()
            if not actor:
                return False
            organization_id = actor["organization_id"]
        conn.execute(
            """INSERT INTO audit_events
               (organization_id, actor_user_id, action, resource_type, resource_id, request_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                organization_id,
                actor_user_id,
                action,
                resource_type,
                str(resource_id)[:100] if resource_id is not None else None,
                str(request_id)[:80] if request_id else None,
            ),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def registrar_versao_documento(
    arquivo_id, storage_key, sha256, media_type, actor_user_id=None, cnes_unidade=None,
    request_id=None,
):
    """Append a document version without overwriting prior stored files."""
    conn = conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT a.organization_id, a.nome_stored FROM arquivos a
               JOIN pacientes p ON p.id=a.paciente_id
               WHERE a.id=? AND (? IS NULL OR p.cnes_unidade=?)""",
            (arquivo_id, cnes_unidade, cnes_unidade),
        ).fetchone()
        if not row:
            conn.rollback()
            return False
        version = conn.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 AS next_version FROM document_versions WHERE arquivo_id=?",
            (arquivo_id,),
        ).fetchone()["next_version"]
        conn.execute(
            """INSERT INTO document_versions
               (organization_id, arquivo_id, version_number, storage_key, sha256, media_type, actor_user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                row["organization_id"], arquivo_id, version, storage_key,
                sha256, media_type, actor_user_id,
            ),
        )
        conn.execute(
            "UPDATE arquivos SET nome_stored=?, mime_type=? WHERE id=?",
            (storage_key, media_type, arquivo_id),
        )
        if actor_user_id is not None:
            conn.execute(
                """INSERT INTO audit_events
                   (organization_id, actor_user_id, action, resource_type, resource_id, request_id)
                   VALUES (?, ?, 'document_version', 'document', ?, ?)""",
                (row["organization_id"], actor_user_id, str(arquivo_id), request_id),
            )
        conn.commit()
        return version
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ===================================================================
# GESTÃO DE USUÁRIOS E AUTENTICAÇÃO
# ===================================================================

def buscar_usuario_por_login(usuario):
    """Busca os dados de um usuário pelo seu login."""
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM usuarios WHERE usuario = ?", (usuario,))
    dado = cursor.fetchone()
    conn.close()
    return dict(dado) if dado else None


def buscar_usuario_por_id(user_id):
    conn = conectar()
    try:
        row = conn.execute("SELECT * FROM usuarios WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ===================================================================
# AUTENTICAÇÃO POR SENHA
# ===================================================================

def autenticar_usuario(usuario, senha):
    """Verifica login e senha. Retorna dict do usuário ou None."""
    user = buscar_usuario_por_login(usuario)
    if not user:
        return None
    senha_hash = user.get("senha_hash") or ""
    if not senha_hash:
        return None
    try:
        if bcrypt.checkpw(senha.encode("utf-8"), senha_hash.encode("utf-8")):
            return user
    except Exception:
        pass
    return None


def definir_senha_usuario(user_id, nova_senha):
    """Define ou atualiza a senha de um usuário (hash bcrypt)."""
    if not nova_senha or len(nova_senha) < 6:
        raise ValueError("A senha deve ter pelo menos 6 caracteres.")
    senha_hash = bcrypt.hashpw(nova_senha.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    conn = conectar()
    try:
        cursor = conn.execute(
            "UPDATE usuarios SET senha_hash=?, session_version=session_version+1 WHERE id=?",
            (senha_hash, user_id),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


# ===================================================================
# AUTENTICAÇÃO POR PIN
# ===================================================================

PIN_MAX_TENTATIVAS = 5
PIN_BLOQUEIO_MINUTOS = 15


def definir_pin_usuario(user_id, pin):
    """Cadastra ou atualiza o PIN de 4 dígitos (hash bcrypt)."""
    if not pin or not pin.isdigit() or len(pin) != 4:
        raise ValueError("O PIN deve conter exatamente 4 dígitos numéricos.")
    pin_hash = bcrypt.hashpw(pin.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    conn = conectar()
    try:
        conn.execute(
            "UPDATE usuarios SET pin_hash=?, pin_tentativas=0, pin_bloqueado_ate=NULL WHERE id=?",
            (pin_hash, user_id),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def verificar_pin_usuario(user_id, pin):
    """Verifica o PIN. Retorna True/False. Bloqueia após tentativas excessivas."""
    conn = conectar()
    try:
        row = conn.execute(
            "SELECT pin_hash, pin_tentativas, pin_bloqueado_ate FROM usuarios WHERE id=?",
            (user_id,),
        ).fetchone()
        if not row or not row["pin_hash"]:
            return False
        agora = datetime.now(timezone.utc)
        bloqueado_ate = row["pin_bloqueado_ate"]
        if bloqueado_ate:
            try:
                dt_bloqueio = datetime.fromisoformat(bloqueado_ate)
                if dt_bloqueio.tzinfo is None:
                    dt_bloqueio = dt_bloqueio.replace(tzinfo=timezone.utc)
                if agora < dt_bloqueio:
                    return False
            except ValueError:
                pass
        try:
            ok = bcrypt.checkpw(pin.encode("utf-8"), row["pin_hash"].encode("utf-8"))
        except Exception:
            ok = False
        if ok:
            conn.execute(
                "UPDATE usuarios SET pin_tentativas=0, pin_bloqueado_ate=NULL WHERE id=?",
                (user_id,),
            )
            conn.commit()
            return True
        tentativas = (row["pin_tentativas"] or 0) + 1
        bloqueio = None
        if tentativas >= PIN_MAX_TENTATIVAS:
            bloqueio = (agora + timedelta(minutes=PIN_BLOQUEIO_MINUTOS)).isoformat()
            tentativas = 0
        conn.execute(
            "UPDATE usuarios SET pin_tentativas=?, pin_bloqueado_ate=? WHERE id=?",
            (tentativas, bloqueio, user_id),
        )
        conn.commit()
        return False
    finally:
        conn.close()


def pin_esta_bloqueado(user_id):
    """Retorna True se o PIN do usuário estiver temporariamente bloqueado."""
    conn = conectar()
    try:
        row = conn.execute(
            "SELECT pin_bloqueado_ate FROM usuarios WHERE id=?", (user_id,)
        ).fetchone()
        if not row or not row["pin_bloqueado_ate"]:
            return False
        try:
            dt = datetime.fromisoformat(row["pin_bloqueado_ate"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) < dt
        except ValueError:
            return False
    finally:
        conn.close()


def buscar_usuario_por_convite(token):
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn = conectar()
    try:
        row = conn.execute(
            """
            SELECT u.* FROM passkey_invites i
            JOIN usuarios u ON u.id = i.user_id
            WHERE i.token_hash = ? AND i.used_at IS NULL AND i.expires_at > ?
            """,
            (token_hash, datetime.now(timezone.utc).isoformat()),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def buscar_credencial_passkey(credential_id):
    conn = conectar()
    try:
        row = conn.execute(
            """
            SELECT c.credential_id, c.user_id, c.credential_data, c.sign_count,
                   u.id,
                   u.nome, u.usuario, u.perfil, u.cnes_unidade, u.passkey_user_handle,
                   u.session_version
            FROM passkey_credentials c
            JOIN usuarios u ON u.id = c.user_id
            WHERE c.credential_id = ?
            """,
            (credential_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def listar_credenciais_passkey(user_id):
    conn = conectar()
    try:
        return conn.execute(
            """
            SELECT credential_data FROM passkey_credentials
            WHERE user_id = ? ORDER BY criado_em
            """,
            (user_id,),
        ).fetchall()
    finally:
        conn.close()


def criar_convite_passkey(user_id, validade_minutos=60):
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(minutes=validade_minutos)).isoformat()
    conn = conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM usuarios WHERE id = ?", (user_id,)).fetchone():
            conn.rollback()
            return None
        conn.execute(
            """
            UPDATE passkey_invites SET used_at = ?
            WHERE user_id = ? AND used_at IS NULL
            """,
            (now.isoformat(), user_id),
        )
        conn.execute(
            """
            INSERT INTO passkey_invites (token_hash, user_id, expires_at)
            VALUES (?, ?, ?)
            """,
            (token_hash, user_id, expires_at),
        )
        conn.commit()
        return token
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def criar_primeiro_admin(nome, usuario, senha=""):
    if not isinstance(nome, str) or not isinstance(usuario, str):
        raise ValueError("Nome e usuário devem ser textos.")
    nome = nome.strip()
    usuario = usuario.strip()
    if not nome or len(nome) > 160 or not usuario or len(usuario) > 80:
        raise ValueError("Informe nome e usuário válidos.")

    senha_hash = ""
    if senha:
        senha_hash = bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    conn = conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM usuarios LIMIT 1").fetchone():
            conn.rollback()
            return False
        conn.execute(
            """
            INSERT INTO usuarios
                (nome, usuario, senha_hash, perfil, registro_profissional, passkey_user_handle)
            VALUES (?, ?, ?, 'ADMIN', 'MASTER', ?)
            """,
            (nome, usuario, senha_hash, secrets.token_bytes(32)),
        )
        conn.commit()
        return True
    except PG_INTEGRITY_ERRORS:
        conn.rollback()
        return False
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def criar_desafio_passkey(ceremony, state, user_id=None, invite_token=None):
    challenge_id = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(minutes=5)).isoformat()
    invite_hash = (
        hashlib.sha256(invite_token.encode("utf-8")).hexdigest()
        if invite_token
        else None
    )
    conn = conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if invite_hash and not conn.execute(
            """
            SELECT 1 FROM passkey_invites
            WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?
            """,
            (invite_hash, now.isoformat()),
        ).fetchone():
            conn.rollback()
            return None
        conn.execute(
            """
            INSERT INTO passkey_challenges
                (id, ceremony, state_json, user_id, invite_hash, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (challenge_id, ceremony, json.dumps(state), user_id, invite_hash, expires_at),
        )
        conn.commit()
        return challenge_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def buscar_desafio_passkey(challenge_id, ceremony):
    conn = conectar()
    try:
        row = conn.execute(
            """
            SELECT id, state_json, user_id, invite_hash
            FROM passkey_challenges
            WHERE id = ? AND ceremony = ? AND used_at IS NULL AND expires_at > ?
            """,
            (challenge_id, ceremony, datetime.now(timezone.utc).isoformat()),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def concluir_registro_passkey(
    challenge_id, credential_id, credential_data, sign_count
):
    now = datetime.now(timezone.utc).isoformat()
    conn = conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        challenge = conn.execute(
            """
            SELECT user_id, invite_hash FROM passkey_challenges
            WHERE id = ? AND ceremony LIKE 'register%' AND used_at IS NULL
                AND expires_at > ?
            """,
            (challenge_id, now),
        ).fetchone()
        if not challenge or not challenge["user_id"]:
            conn.rollback()
            return False
        if challenge["invite_hash"] and not conn.execute(
            """
            SELECT 1 FROM passkey_invites
            WHERE token_hash = ? AND user_id = ? AND used_at IS NULL
                AND expires_at > ?
            """,
            (challenge["invite_hash"], challenge["user_id"], now),
        ).fetchone():
            conn.rollback()
            return False
        if challenge["invite_hash"]:
            conn.execute(
                "DELETE FROM passkey_credentials WHERE user_id = ?",
                (challenge["user_id"],),
            )
            conn.execute(
                "UPDATE usuarios SET session_version = session_version + 1 WHERE id = ?",
                (challenge["user_id"],),
            )
        conn.execute(
            "UPDATE passkey_challenges SET used_at = ? WHERE id = ?",
            (now, challenge_id),
        )
        if challenge["invite_hash"]:
            conn.execute(
                "UPDATE passkey_invites SET used_at = ? WHERE token_hash = ?",
                (now, challenge["invite_hash"]),
            )
        conn.execute(
            """
            INSERT INTO passkey_credentials
                (credential_id, user_id, credential_data, sign_count)
            VALUES (?, ?, ?, ?)
            """,
            (credential_id, challenge["user_id"], credential_data, sign_count),
        )
        conn.commit()
        return True
    except PG_INTEGRITY_ERRORS:
        conn.rollback()
        return False
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def concluir_login_passkey(challenge_id, user_id, credential_id, sign_count):
    now = datetime.now(timezone.utc).isoformat()
    conn = conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        challenge = conn.execute(
            """
            SELECT id FROM passkey_challenges
            WHERE id = ? AND ceremony = 'login' AND used_at IS NULL AND expires_at > ?
            """,
            (challenge_id, now),
        ).fetchone()
        credential = conn.execute(
            """
            SELECT c.sign_count, u.id FROM passkey_credentials c
            JOIN usuarios u ON u.id = c.user_id
            WHERE c.credential_id = ? AND c.user_id = ?
            """,
            (credential_id, user_id),
        ).fetchone()
        if not challenge or not credential:
            conn.rollback()
            return False
        previous_count = credential["sign_count"]
        if previous_count and sign_count and sign_count <= previous_count:
            conn.rollback()
            return False
        conn.execute(
            "UPDATE passkey_challenges SET used_at = ? WHERE id = ?",
            (now, challenge_id),
        )
        conn.execute(
            "UPDATE passkey_credentials SET sign_count = ? WHERE credential_id = ?",
            (sign_count, credential_id),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def backup_database(backup_dir):
    """Create and verify a consistent SQLite backup without replacing existing files."""
    source_path = Path(DB_NAME).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"Banco de dados não encontrado: {source_path}")

    directory = Path(backup_dir)
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = directory / f"{source_path.stem}-before-admin-recovery-{timestamp}.db"

    source = sqlite3.connect(str(source_path), timeout=30)
    destination = sqlite3.connect(str(backup_path))
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()

    with sqlite3.connect(str(backup_path)) as verification:
        result = verification.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise sqlite3.DatabaseError("A verificação de integridade do backup falhou.")
    return str(backup_path)


def cadastrar_usuario(
    nome, usuario, perfil, registro_profissional="", cnes_unidade=""
):
    """Create an account without a password; access is granted by a passkey invite."""
    if not isinstance(nome, str) or not isinstance(usuario, str):
        raise ValueError("Nome e usuário devem ser textos.")
    if (
        not isinstance(perfil, str)
        or not isinstance(registro_profissional, str)
        or not isinstance(cnes_unidade, str)
    ):
        raise ValueError("Perfil, registro e CNES devem ser textos.")
    nome = nome.strip()
    usuario = usuario.strip()
    perfil = perfil.strip().upper()
    registro_profissional = registro_profissional.strip()
    cnes_unidade = cnes_unidade.strip()
    if not nome or len(nome) > 160:
        raise ValueError("Informe um nome válido (até 160 caracteres).")
    if not usuario or len(usuario) > 80:
        raise ValueError("Informe um usuário válido (até 80 caracteres).")
    if perfil not in ALLOWED_USER_PROFILES:
        raise ValueError("Perfil de usuário inválido.")
    if perfil != "ADMIN" and not cnes_unidade:
        raise ValueError("Vincule o profissional a uma unidade CNES.")
    if cnes_unidade:
        if len(cnes_unidade) != 7 or not cnes_unidade.isdigit():
            raise ValueError("O CNES deve conter 7 dígitos.")
        unit = conectar()
        try:
            exists = unit.execute(
                "SELECT 1 FROM dv_unidades WHERE cnes = ?", (cnes_unidade,)
            ).fetchone()
        finally:
            unit.close()
        if not exists:
            raise ValueError("Selecione uma unidade CNES cadastrada.")
        garantir_organizacao_unidade(cnes_unidade, "Unidade de Saúde")
    if len(registro_profissional) > 80:
        raise ValueError("O registro profissional deve ter até 80 caracteres.")
    conn = conectar()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO usuarios
                (nome, usuario, senha_hash, perfil, registro_profissional, cnes_unidade,
                 organization_id, passkey_user_handle)
            VALUES (?, ?, '', ?, ?, ?,
                    (SELECT organization_id FROM dv_unidades WHERE cnes=?), ?)
        """, (
            nome,
            usuario,
            perfil,
            registro_profissional,
            cnes_unidade or None,
            cnes_unidade or None,
            secrets.token_bytes(32),
        ))
        conn.commit()
        sucesso = True
    except PG_INTEGRITY_ERRORS:
        sucesso = False  # Usuário já existente
    conn.close()
    return sucesso


def listar_usuarios():
    """Retorna a lista de todos os usuários cadastrados."""
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT u.id, u.nome, u.usuario, u.perfil, u.registro_profissional,
               u.cnes_unidade,
               (SELECT COUNT(*) FROM passkey_credentials c WHERE c.user_id = u.id)
                   AS passkey_count
        FROM usuarios u WHERE u.perfil <> 'DCA_SUPER_ADMIN' ORDER BY u.nome
        """
    )
    dados = cursor.fetchall()
    conn.close()
    return [dict(x) for x in dados]


def listar_unidades():
    conn = conectar()
    try:
        rows = conn.execute("SELECT cnes, nome FROM dv_unidades ORDER BY nome").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# ===================================================================
# FUNÇÕES DE CÁLCULO E AUXILIARES
# ===================================================================

def calcular_imc(peso, altura):
    """Calcula o IMC e retorna o valor numérico e a classificação."""
    try:
        if not peso or not altura:
            return None, ""

        p = float(peso)
        a = float(altura)

        if a > 3.0:
            a = a / 100.0

        if a <= 0 or p <= 0:
            return None, ""

        imc = p / (a**2)

        if imc < 18.5:
            classe = "Abaixo do peso"
        elif imc < 25:
            classe = "Peso normal"
        elif imc < 30:
            classe = "Sobrepeso"
        elif imc < 35:
            classe = "Obesidade Grau I"
        elif imc < 40:
            classe = "Obesidade Grau II"
        else:
            classe = "Obesidade Grau III"

        return round(imc, 2), classe

    except (ValueError, TypeError, ZeroDivisionError):
        return None, ""


# ===================================================================
# OPERAÇÕES DE PACIENTES
# ===================================================================

def adicionar_paciente(
    nome,
    telefone="",
    cpf="",
    cns="",
    cnes_unidade=None,
    data_nascimento="",
    sexo="",
    bairro="",
    peso=0,
    altura=0,
    pressao="",
):
    if cnes_unidade:
        if (
            not isinstance(cnes_unidade, str)
            or len(cnes_unidade) != 7
            or not cnes_unidade.isdigit()
        ):
            raise ValueError("Vincule o paciente a uma unidade CNES válida.")
        conn = conectar()
        try:
            if not conn.execute(
                "SELECT 1 FROM dv_unidades WHERE cnes=?", (cnes_unidade,)
            ).fetchone():
                raise ValueError("Selecione uma unidade CNES cadastrada.")
        finally:
            conn.close()
        garantir_organizacao_unidade(cnes_unidade, "Unidade de Saúde")

    imc, classe = calcular_imc(peso, altura)

    conn = conectar()
    try:
        cursor = conn.execute(
            """
            INSERT INTO pacientes
                (nome, telefone, cpf, cns, cnes_unidade, organization_id, data_nascimento, sexo,
                 bairro, peso, altura, pressao, imc, classificacao_imc)
            VALUES (?, ?, ?, ?, ?, (SELECT organization_id FROM dv_unidades WHERE cnes=?),
                    ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nome,
                telefone,
                cpf,
                cns,
                cnes_unidade,
                cnes_unidade,
                data_nascimento,
                sexo,
                bairro,
                peso,
                altura,
                pressao,
                imc,
                classe,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def list_pacientes(cnes_unidade=None):
    conn = conectar()
    try:
        if cnes_unidade is None:
            dados = conn.execute("SELECT * FROM pacientes ORDER BY nome").fetchall()
        else:
            dados = conn.execute(
                "SELECT * FROM pacientes WHERE cnes_unidade=? ORDER BY nome",
                (cnes_unidade,),
            ).fetchall()
        return [dict(x) for x in dados]
    finally:
        conn.close()


def list_pacientes_paginado(cnes_unidade=None, page=1, page_size=50, busca=""):
    """Retorna uma página de pacientes e o total de resultados disponíveis."""
    page = max(1, int(page))
    page_size = min(100, max(1, int(page_size)))
    busca = (busca or "").strip()
    params = []
    filters = []
    if cnes_unidade is not None:
        filters.append("cnes_unidade=?")
        params.append(cnes_unidade)
    if busca:
        filters.append("(nome LIKE ? OR cpf LIKE ?)")
        params.extend((f"%{busca}%", f"%{busca}%"))
    where = f" WHERE {' AND '.join(filters)}" if filters else ""
    conn = conectar()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS total FROM pacientes{where}", params
        ).fetchone()["total"]
        rows = conn.execute(
            f"SELECT * FROM pacientes{where} ORDER BY nome LIMIT ? OFFSET ?",
            (*params, page_size, (page - 1) * page_size),
        ).fetchall()
        return [dict(row) for row in rows], total
    finally:
        conn.close()


def get_paciente(id_paciente, cnes_unidade=None):
    conn = conectar()
    try:
        if cnes_unidade is None:
            dado = conn.execute(
                "SELECT * FROM pacientes WHERE id=?", (id_paciente,)
            ).fetchone()
        else:
            dado = conn.execute(
                "SELECT * FROM pacientes WHERE id=? AND cnes_unidade=?",
                (id_paciente, cnes_unidade),
            ).fetchone()
        return dict(dado) if dado else None
    finally:
        conn.close()


def atualizar_paciente(id_paciente, dados, cnes_unidade=None):
    peso = dados.get("peso", 0)
    altura = dados.get("altura", 0)
    imc, classe = calcular_imc(peso, altura)

    assigned_cnes = dados.get("cnes_unidade") if cnes_unidade is None else cnes_unidade
    conn = conectar()
    try:
        if "cnes_unidade" not in dados and cnes_unidade is None:
            existing = conn.execute(
                "SELECT cnes_unidade FROM pacientes WHERE id=?", (id_paciente,)
            ).fetchone()
            if not existing:
                return False
            assigned_cnes = existing["cnes_unidade"]
        where = "id=?"
        parameters = []
        if cnes_unidade is not None:
            where += " AND cnes_unidade=?"
            parameters.append(cnes_unidade)
        query = """
    UPDATE pacientes
    SET
        nome=?,
        telefone=?,
        cpf=?,
        cns=?,
        cnes_unidade=?,
        organization_id=(SELECT organization_id FROM dv_unidades WHERE cnes=?),
        data_nascimento=?,
        sexo=?,
        bairro=?,
        pressao=?,
        peso=?,
        altura=?,
        imc=?,
        classificacao_imc=?
    WHERE """ + where
        parameters = [
            dados.get("nome"),
            dados.get("telefone"),
            dados.get("cpf"),
            dados.get("cns"),
            assigned_cnes,
            assigned_cnes,
            dados.get("data_nascimento"),
            dados.get("sexo"),
            dados.get("bairro"),
            dados.get("pressao"),
            peso,
            altura,
            imc,
            classe,
            id_paciente,
        ]
        if cnes_unidade is not None:
            parameters.append(cnes_unidade)
        conn.execute(query, parameters)
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


# ===================================================================
# OPERAÇÕES DE ARQUIVOS / ANEXOS
# ===================================================================

def adicionar_arquivo(
    paciente_id,
    tipo,
    nome_original,
    nome_stored,
    mime_type,
    descricao="",
    cnes_unidade=None,
    sha256=None,
    actor_user_id=None,
    request_id=None,
    audit_action="document_upload",
):
    conn = conectar()
    try:
        cursor = conn.execute(
            """
            INSERT INTO arquivos
                (paciente_id, tipo, nome_original, nome_stored, mime_type, descricao,
                 criado_em, organization_id)
            SELECT ?, ?, ?, ?, ?, ?, ?, p.organization_id
            FROM pacientes p
            WHERE p.id = ? AND (? IS NULL OR p.cnes_unidade = ?)
            """,
            (
                paciente_id,
                tipo,
                nome_original,
                nome_stored,
                mime_type,
                descricao,
                datetime.now().strftime("%d/%m/%Y %H:%M"),
                paciente_id,
                cnes_unidade,
                cnes_unidade,
            ),
        )
        if cursor.rowcount == 1:
            conn.execute(
                """INSERT INTO document_versions
                   (organization_id, arquivo_id, version_number, storage_key, sha256,
                    media_type, actor_user_id)
                   VALUES ((SELECT organization_id FROM pacientes WHERE id=?), ?, 1, ?, ?, ?, ?)""",
                (
                    paciente_id, cursor.lastrowid, nome_stored, sha256,
                    mime_type, actor_user_id,
                ),
            )
            if actor_user_id is not None:
                conn.execute(
                    """INSERT INTO audit_events
                       (organization_id, actor_user_id, action, resource_type, resource_id, request_id)
                       SELECT organization_id, ?, ?, 'document', ?, ?
                       FROM usuarios WHERE id=?""",
                    (
                        actor_user_id, audit_action, str(cursor.lastrowid),
                        request_id, actor_user_id,
                    ),
                )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def listar_arquivos_paciente(paciente_id, cnes_unidade=None):
    conn = conectar()
    try:
        dados = conn.execute(
            """
            SELECT a.* FROM arquivos a
            JOIN pacientes p ON p.id = a.paciente_id
            WHERE a.paciente_id = ? AND (? IS NULL OR p.cnes_unidade = ?)
            ORDER BY a.id DESC
            """,
            (paciente_id, cnes_unidade, cnes_unidade),
        ).fetchall()
        return [dict(x) for x in dados]
    finally:
        conn.close()


# ===================================================================
# OPERAÇÕES DE ODONTOGRAMA
# ===================================================================

def salvar_condicao_dente(
    paciente_id, dente, face, condicao, codigo_sigtap="", cnes_unidade=None
):
    conn = conectar()
    try:
        conn.execute(
            """
            DELETE FROM odontograma
            WHERE paciente_id=? AND dente=? AND face=?
                AND EXISTS (
                    SELECT 1 FROM pacientes p
                    WHERE p.id=odontograma.paciente_id
                        AND (? IS NULL OR p.cnes_unidade=?)
                )
            """,
            (paciente_id, dente, face, cnes_unidade, cnes_unidade),
        )
        cursor = conn.execute(
            """
            INSERT INTO odontograma (paciente_id, dente, face, condicao, codigo_sigtap)
            SELECT ?, ?, ?, ?, ?
            WHERE EXISTS (
                SELECT 1 FROM pacientes
                WHERE id=? AND (? IS NULL OR cnes_unidade=?)
            )
            """,
            (
                paciente_id,
                dente,
                face,
                condicao,
                codigo_sigtap,
                paciente_id,
                cnes_unidade,
                cnes_unidade,
            ),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def obter_odontograma_paciente(paciente_id, cnes_unidade=None):
    conn = conectar()
    try:
        dados = conn.execute(
            """
            SELECT o.* FROM odontograma o
            JOIN pacientes p ON p.id = o.paciente_id
            WHERE o.paciente_id=? AND (? IS NULL OR p.cnes_unidade=?)
            """,
            (paciente_id, cnes_unidade, cnes_unidade),
        ).fetchall()
    finally:
        conn.close()

    resultado = {}
    for r in dados:
        dente = str(r["dente"])
        if dente not in resultado:
            resultado[dente] = {}
        resultado[dente][r["face"]] = r["condicao"]

    return resultado


# ===================================================================
# OPERAÇÕES DA AGENDA
# ===================================================================

def obter_agenda(cnes_unidade=None):
    conn = conectar()
    try:
        dados = conn.execute(
            """
            SELECT a.* FROM agenda a
            JOIN pacientes p ON p.id = a.paciente_id
            WHERE ? IS NULL OR p.cnes_unidade=?
            ORDER BY a.data_hora
            """,
            (cnes_unidade, cnes_unidade),
        ).fetchall()
        return [dict(x) for x in dados]
    finally:
        conn.close()


def excluir_agendamento(agendamento_id, cnes_unidade=None):
    conn = conectar()
    try:
        cursor = conn.execute(
            """
            DELETE FROM agenda WHERE id=?
            AND (? IS NULL OR EXISTS (
                SELECT 1 FROM pacientes p WHERE p.id=agenda.paciente_id AND p.cnes_unidade=?
            ))
            """,
            (agendamento_id, cnes_unidade, cnes_unidade),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def adicionar_agendamento(paciente_id, data_hora, especialidade, procedimento, cnes_unidade=None):
    conn = conectar()
    try:
        cursor = conn.execute(
            """
            INSERT INTO agenda (paciente_id, data_hora, especialidade, procedimento)
            SELECT ?, ?, ?, ?
            WHERE EXISTS (
                SELECT 1 FROM pacientes
                WHERE id=? AND (? IS NULL OR cnes_unidade=?)
            )
            """,
            (
                paciente_id,
                data_hora,
                especialidade,
                procedimento,
                paciente_id,
                cnes_unidade,
                cnes_unidade,
            ),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


# ===================================================================
# OPERAÇÕES DO PRONTUÁRIO SOAP
# ===================================================================

def salvar_soap(
    paciente_id, subjetivo, objetivo, avaliacao, plano, cnes_unidade=None
):
    conn = conectar()
    try:
        cursor = conn.execute(
            """
            INSERT INTO prontuario_soap
                (paciente_id, subjetivo, objetivo, avaliacao, plano)
            SELECT ?, ?, ?, ?, ?
            WHERE EXISTS (
                SELECT 1 FROM pacientes
                WHERE id=? AND (? IS NULL OR cnes_unidade=?)
            )
            """,
            (
                paciente_id,
                subjetivo,
                objetivo,
                avaliacao,
                plano,
                paciente_id,
                cnes_unidade,
                cnes_unidade,
            ),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def listar_soap_paciente(paciente_id, cnes_unidade=None):
    conn = conectar()
    try:
        registros = conn.execute(
            """
            SELECT s.* FROM prontuario_soap s
            JOIN pacientes p ON p.id = s.paciente_id
            WHERE s.paciente_id = ? AND (? IS NULL OR p.cnes_unidade = ?)
            ORDER BY s.data_atendimento DESC
            """,
            (paciente_id, cnes_unidade, cnes_unidade),
        ).fetchall()
        return registros
    finally:
        conn.close()


# ===================================================================
# ESTATÍSTICAS DO DASHBOARD
# ===================================================================

def obter_estatisticas_dashboard():
    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM pacientes")
    total_pacientes = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM agenda")
    total_agendamentos = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM prontuario_soap")
    total_prontuarios = cursor.fetchone()[0]

    cursor.execute("""
        SELECT procedimento, COUNT(*) as qtd 
        FROM agenda 
        WHERE procedimento IS NOT NULL AND procedimento != ''
        GROUP BY procedimento 
        ORDER BY qtd DESC LIMIT 5
    """)
    procedimentos_top = cursor.fetchall()

    conn.close()

    return {
        "pacientes": total_pacientes,
        "agendamentos": total_agendamentos,
        "prontuarios": total_prontuarios,
        "procedimentos_labels": [p[0] for p in procedimentos_top] if procedimentos_top else ["Nenhum procedimento registrado"],
        "procedimentos_qtds": [p[1] for p in procedimentos_top] if procedimentos_top else [0]
    }


def provisionar_admin_inicial():
    """Cria o admin inicial a partir de variáveis de ambiente, se o banco estiver vazio."""
    usuario = os.environ.get("ADMIN_USUARIO", "admin")
    senha = os.environ.get("ADMIN_SENHA", "")
    pin = os.environ.get("ADMIN_PIN", "")
    nome = os.environ.get("ADMIN_NOME", "Administrador")
    if not senha:
        return
    conn = conectar()
    try:
        existe = conn.execute("SELECT id FROM usuarios WHERE usuario=?", (usuario,)).fetchone()
        if existe:
            return
        senha_hash = bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        conn.execute(
            """
            INSERT INTO usuarios
                (nome, usuario, senha_hash, perfil, registro_profissional, passkey_user_handle)
            VALUES (?, ?, ?, 'ADMIN', 'MASTER', ?)
            """,
            (nome, usuario, senha_hash, secrets.token_bytes(32)),
        )
        conn.commit()
        if pin and pin.isdigit() and len(pin) == 4:
            row = conn.execute("SELECT id FROM usuarios WHERE usuario=?", (usuario,)).fetchone()
            pin_hash = bcrypt.hashpw(pin.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            conn.execute("UPDATE usuarios SET pin_hash=? WHERE id=?", (pin_hash, row["id"]))
            conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


# Initialize the schema and provision the first administrator if configured.
criar_tabelas()
provisionar_admin_inicial()
