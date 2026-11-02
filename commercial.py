"""Commercial account and subscription foundations for DCA Software.

This module intentionally has no payment gateway integration. New subscriptions
remain pending until a future, explicitly configured provider confirms payment.
"""

from __future__ import annotations

import os
import re
import secrets
import uuid
import base64
import hashlib
import io
import time
from datetime import datetime, timezone
from pathlib import Path

import bcrypt
import pyotp
import qrcode
from flask import Blueprint, abort, current_app, make_response, redirect, render_template_string, request, session, url_for
from markupsafe import escape

import database

commercial_bp = Blueprint("commercial", __name__)


MODULES = (
    ("medico", "DCA Médico", "Atendimento médico SOAP já existente", "/prontuario"),
    ("odonto", "DCA Odonto", "Atendimento odontológico", "/odonto/"),
    ("farma", "DCA Farma", "Farmácia e dispensação", "/farmacia-sus/"),
    ("gestao", "DCA Gestão", "Gestão da organização", "/cliente/"),
    ("prontuario", "DCA Prontuário", "Cadastros e documentos do paciente", "/pacientes"),
    ("estoque", "DCA Estoque", "Estoque e lotes", "/farmacia-sus/estoque/"),
    ("relatorios", "DCA Relatórios", "Relatórios operacionais", "/farmacia-sus/relatorios/"),
)
MEMBER_PROFILES = (
    "DENTISTA", "MEDICO", "ENFERMEIRO", "ENFERMEIRA", "PSICOLOGO",
    "FARMACIA", "FARMACEUTICO", "RECEPCAO",
)


def ensure_schema(seed_catalog: bool = True) -> None:
    """Create commercial tables additively and seed the single general plan."""
    conn = database.conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if database._USE_POSTGRES:
            conn.execute("SELECT pg_advisory_xact_lock(731904263)")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS dca_modules (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                route TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS dca_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                price_cents INTEGER,
                billing_interval TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (price_cents IS NULL OR price_cents >= 0)
            );
            CREATE TABLE IF NOT EXISTS dca_plan_modules (
                plan_id INTEGER NOT NULL REFERENCES dca_plans(id),
                module_code TEXT NOT NULL REFERENCES dca_modules(code),
                PRIMARY KEY (plan_id, module_code)
            );
            CREATE TABLE IF NOT EXISTS dca_organization_modules (
                organization_id INTEGER NOT NULL REFERENCES organizacoes(id),
                module_code TEXT NOT NULL REFERENCES dca_modules(code),
                enabled INTEGER NOT NULL DEFAULT 1,
                changed_by INTEGER REFERENCES usuarios(id),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (organization_id, module_code)
            );
            CREATE TABLE IF NOT EXISTS dca_organization_profiles (
                organization_id INTEGER PRIMARY KEY REFERENCES organizacoes(id),
                account_type TEXT NOT NULL CHECK (account_type IN ('INDIVIDUAL', 'ESTABLISHMENT')),
                primary_specialty TEXT NOT NULL,
                expected_professionals INTEGER NOT NULL,
                declared_units INTEGER NOT NULL,
                billing_contact_name TEXT NOT NULL,
                billing_email TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (expected_professionals > 0),
                CHECK (declared_units >= 0)
            );
            CREATE TABLE IF NOT EXISTS dca_organization_specialties (
                organization_id INTEGER NOT NULL REFERENCES organizacoes(id),
                specialty TEXT NOT NULL,
                is_primary INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (organization_id, specialty)
            );
            CREATE TABLE IF NOT EXISTS dca_memberships (
                organization_id INTEGER NOT NULL REFERENCES organizacoes(id),
                user_id INTEGER NOT NULL REFERENCES usuarios(id),
                member_role TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (organization_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS dca_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL UNIQUE REFERENCES organizacoes(id),
                plan_id INTEGER NOT NULL REFERENCES dca_plans(id),
                status TEXT NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING', 'UNDER_REVIEW', 'ACTIVE', 'PAST_DUE', 'CANCELLED', 'SUSPENDED')),
                gateway_name TEXT,
                gateway_subscription_id TEXT,
                next_renewal_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS dca_subscription_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subscription_id INTEGER NOT NULL REFERENCES dca_subscriptions(id),
                actor_user_id INTEGER REFERENCES usuarios(id),
                event_type TEXT NOT NULL,
                request_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS dca_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subscription_id INTEGER NOT NULL REFERENCES dca_subscriptions(id),
                gateway_name TEXT,
                gateway_payment_id TEXT,
                gateway_event_id TEXT UNIQUE,
                amount_cents INTEGER,
                currency TEXT,
                status TEXT NOT NULL,
                confirmed_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (amount_cents IS NULL OR amount_cents >= 0)
            );
            CREATE TABLE IF NOT EXISTS dca_commercial_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER REFERENCES organizacoes(id),
                actor_user_id INTEGER REFERENCES usuarios(id),
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT,
                request_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS dca_super_admin_mfa (
                user_id INTEGER PRIMARY KEY REFERENCES usuarios(id),
                secret_encrypted TEXT,
                enabled INTEGER NOT NULL DEFAULT 0,
                last_counter INTEGER NOT NULL DEFAULT -1,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (enabled IN (0, 1))
            );
            CREATE TABLE IF NOT EXISTS dca_super_admin_recovery_codes (
                user_id INTEGER NOT NULL REFERENCES usuarios(id),
                code_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, code_hash)
            );
            CREATE INDEX IF NOT EXISTS idx_dca_subscriptions_status ON dca_subscriptions(status);
            CREATE INDEX IF NOT EXISTS idx_dca_memberships_user ON dca_memberships(user_id);
            CREATE INDEX IF NOT EXISTS idx_dca_commercial_audit_org ON dca_commercial_audit(organization_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_dca_mfa_lockout ON dca_super_admin_mfa(locked_until);
            """
        )
        if seed_catalog:
            for code, name, description, route in MODULES:
                conn.execute(
                    """INSERT INTO dca_modules(code, name, description, route)
                       VALUES (?, ?, ?, ?) ON CONFLICT(code) DO NOTHING""",
                    (code, name, description, route),
                )
            conn.execute(
                """INSERT INTO dca_plans(code, name, description, price_cents, billing_interval)
                   VALUES ('GENERAL', 'DCA SOFTWARE — PLANO GERAL',
                           'Plataforma integrada com módulos profissionais habilitáveis.', NULL, NULL)
                   ON CONFLICT(code) DO NOTHING"""
            )
            plan_id = conn.execute(
                "SELECT id FROM dca_plans WHERE code='GENERAL'"
            ).fetchone()["id"]
            for code, _name, _description, _route in MODULES:
                conn.execute(
                    """INSERT INTO dca_plan_modules(plan_id, module_code)
                       VALUES (?, ?) ON CONFLICT(plan_id, module_code) DO NOTHING""",
                    (plan_id, code),
                )
            for tenant in conn.execute(
                "SELECT organization_id FROM dca_subscriptions"
            ).fetchall():
                for code, _name, _description, _route in MODULES:
                    conn.execute(
                        """INSERT INTO dca_organization_modules(organization_id, module_code, enabled)
                           VALUES (?, ?, 1) ON CONFLICT(organization_id, module_code) DO NOTHING""",
                        (tenant["organization_id"], code),
                    )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def provision_super_admin() -> None:
    """Provision the distinct commercial role only from explicit environment values."""
    username = (os.environ.get("DCA_SUPER_ADMIN_USER") or "").strip().lower()
    full_name = (os.environ.get("DCA_SUPER_ADMIN_NAME") or "").strip()
    password = os.environ.get("DCA_SUPER_ADMIN_PASSWORD") or ""
    if not username and not password and not full_name:
        return
    if not username or not full_name or len(username) > 80 or len(full_name) > 160:
        raise RuntimeError("Configure DCA_SUPER_ADMIN_USER e DCA_SUPER_ADMIN_NAME juntos.")
    if len(password) < 14 or len(password.encode("utf-8")) > 72:
        raise RuntimeError("DCA_SUPER_ADMIN_PASSWORD deve ter de 14 a 72 bytes.")
    existing = database.buscar_usuario_por_login(username)
    if existing:
        if existing.get("perfil") != "DCA_SUPER_ADMIN":
            raise RuntimeError("O login configurado para Super Admin DCA já pertence a outro perfil.")
        return
    conn = database.conectar()
    try:
        conn.execute(
            """INSERT INTO usuarios
               (nome, usuario, senha_hash, perfil, registro_profissional,
                organization_id, passkey_user_handle)
               VALUES (?, ?, ?, 'DCA_SUPER_ADMIN', 'DCA', NULL, ?)
               ON CONFLICT(usuario) DO NOTHING""",
            (
                full_name,
                username,
                bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
                secrets.token_bytes(32),
            ),
        )
        row = conn.execute(
            "SELECT perfil FROM usuarios WHERE usuario=?", (username,)
        ).fetchone()
        if not row or row["perfil"] != "DCA_SUPER_ADMIN":
            raise RuntimeError("Não foi possível provisionar uma conta DCA Super Admin exclusiva.")
        conn.commit()
    finally:
        conn.close()


def _mfa_fernet():
    """Derive the at-rest MFA encryption key from the deployment SECRET_KEY."""
    from cryptography.fernet import Fernet

    secret_key = current_app.config.get("SECRET_KEY") or os.environ.get("SECRET_KEY")
    if not secret_key:
        raise RuntimeError("SECRET_KEY Ã© necessÃ¡ria para proteger segredos MFA.")
    material = hashlib.sha256(
        b"dca-super-admin-totp-v1\0" + str(secret_key).encode("utf-8")
    ).digest()
    return Fernet(base64.urlsafe_b64encode(material))


def _valid_super_admin(user_id: int) -> bool:
    user = database.buscar_usuario_por_id(user_id)
    return bool(user and user.get("perfil") == "DCA_SUPER_ADMIN")


def mfa_enabled(user_id: int) -> bool:
    conn = database.conectar()
    try:
        row = conn.execute(
            "SELECT enabled FROM dca_super_admin_mfa WHERE user_id=?", (user_id,)
        ).fetchone()
        return bool(row and row["enabled"])
    finally:
        conn.close()


def begin_mfa_setup(user_id: int) -> str:
    """Create or resume an encrypted, not-yet-active TOTP enrollment."""
    if not _valid_super_admin(user_id):
        raise PermissionError("Perfil Super Admin DCA obrigatÃ³rio.")
    conn = database.conectar()
    try:
        conn.execute(
            "INSERT INTO dca_super_admin_mfa(user_id) VALUES (?) ON CONFLICT(user_id) DO NOTHING",
            (user_id,),
        )
        row = conn.execute(
            "SELECT secret_encrypted, enabled FROM dca_super_admin_mfa WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if row["enabled"]:
            raise ValueError("O MFA jÃ¡ estÃ¡ ativo para esta conta.")
        if row["secret_encrypted"]:
            secret = _mfa_fernet().decrypt(row["secret_encrypted"].encode()).decode()
        else:
            secret = pyotp.random_base32()
            encrypted = _mfa_fernet().encrypt(secret.encode()).decode()
            conn.execute(
                "UPDATE dca_super_admin_mfa SET secret_encrypted=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
                (encrypted, user_id),
            )
        conn.commit()
        return secret
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def activate_mfa(user_id: int, code: str) -> list[str] | None:
    """Activate TOTP after proof of possession and return one-time recovery codes."""
    conn = database.conectar()
    codes: list[str] = []
    try:
        row = conn.execute(
            "SELECT secret_encrypted, enabled, locked_until FROM dca_super_admin_mfa WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if not row or row["enabled"] or not row["secret_encrypted"]:
            return None
        if int(row["locked_until"] or 0) > int(time.time()):
            return None
        secret = _mfa_fernet().decrypt(row["secret_encrypted"].encode()).decode()
        totp = pyotp.TOTP(secret)
        now = int(time.time())
        matched_counter = next(
            (counter for counter in range(now // totp.interval - 1, now // totp.interval + 2)
             if secrets.compare_digest(totp.at(counter * totp.interval), str(code).strip())),
            None,
        )
        if matched_counter is None:
            conn.execute(
                "UPDATE dca_super_admin_mfa SET failed_attempts=failed_attempts+1, "
                "locked_until=CASE WHEN failed_attempts+1>=5 THEN ? ELSE locked_until END "
                "WHERE user_id=?",
                (now + 900, user_id),
            )
            conn.commit()
            return None
        activated = conn.execute(
            "UPDATE dca_super_admin_mfa SET enabled=1, last_counter=?, failed_attempts=0, "
            "locked_until=0, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND enabled=0 AND last_counter<?",
            (matched_counter, user_id, matched_counter),
        )
        if activated.rowcount != 1:
            conn.rollback()
            return None
        for _ in range(10):
            recovery_code = secrets.token_hex(5).upper()
            codes.append(f"{recovery_code[:5]}-{recovery_code[5:]}")
        conn.execute("DELETE FROM dca_super_admin_recovery_codes WHERE user_id=?", (user_id,))
        for recovery_code in codes:
            code_hash = hashlib.sha256(recovery_code.replace("-", "").encode()).hexdigest()
            conn.execute(
                "INSERT INTO dca_super_admin_recovery_codes(user_id, code_hash) VALUES (?, ?)",
                (user_id, code_hash),
            )
        conn.commit()
        return codes
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def verify_mfa(user_id: int, supplied: str) -> bool:
    """Verify a fresh TOTP or consume one hashed recovery code with lockout."""
    conn = database.conectar()
    now = int(time.time())
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT secret_encrypted, enabled, last_counter, failed_attempts, locked_until "
            "FROM dca_super_admin_mfa WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if not row or not row["enabled"] or int(row["locked_until"] or 0) > now:
            conn.rollback()
            return False

        normalized = str(supplied or "").strip().replace("-", "").upper()
        matched_counter = None
        if len(normalized) == 6 and normalized.isdigit():
            secret = _mfa_fernet().decrypt(row["secret_encrypted"].encode()).decode()
            totp = pyotp.TOTP(secret)
            current = now // totp.interval
            matched_counter = next(
                (counter for counter in range(current - 1, current + 2)
                 if counter > int(row["last_counter"] or -1)
                 and secrets.compare_digest(totp.at(counter * totp.interval), normalized)),
                None,
            )
        if matched_counter is not None:
            updated = conn.execute(
                "UPDATE dca_super_admin_mfa SET last_counter=?, failed_attempts=0, locked_until=0, "
                "updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND last_counter<?",
                (matched_counter, user_id, matched_counter),
            )
            if updated.rowcount == 1:
                conn.commit()
                return True

        code_hash = hashlib.sha256(normalized.encode()).hexdigest()
        deleted = conn.execute(
            "DELETE FROM dca_super_admin_recovery_codes WHERE user_id=? AND code_hash=?",
            (user_id, code_hash),
        )
        if deleted.rowcount == 1:
            conn.execute(
                "UPDATE dca_super_admin_mfa SET failed_attempts=0, locked_until=0, "
                "updated_at=CURRENT_TIMESTAMP WHERE user_id=?", (user_id,)
            )
            conn.commit()
            return True

        failures = int(row["failed_attempts"] or 0) + 1
        lock_until = now + 900 if failures >= 5 else int(row["locked_until"] or 0)
        conn.execute(
            "UPDATE dca_super_admin_mfa SET failed_attempts=?, locked_until=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE user_id=?",
            (failures, lock_until, user_id),
        )
        conn.commit()
        return False
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def register_customer(data: dict, request_id: str | None = None) -> int:
    """Create an organization, its owner and one pending general subscription atomically."""
    name = str(data.get("organization_name", "")).strip()
    contact_name = str(data.get("contact_name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    account_type = str(data.get("account_type", "")).strip().upper()
    primary = str(data.get("primary_specialty", "")).strip()
    specialties_raw = str(data.get("specialties", ""))
    password = str(data.get("password", ""))
    try:
        professionals = int(data.get("professional_count", ""))
        units = int(data.get("unit_count", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("Informe quantidades válidas de profissionais e unidades.") from exc

    if account_type not in {"INDIVIDUAL", "ESTABLISHMENT"}:
        raise ValueError("Selecione profissional individual ou estabelecimento.")
    if not name or len(name) > 160 or not contact_name or len(contact_name) > 160:
        raise ValueError("Informe o nome da organização e do responsável.")
    if len(email) > 80 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("Informe um e-mail válido com até 80 caracteres.")
    if not primary or len(primary) > 100:
        raise ValueError("Informe a especialidade principal.")
    if professionals < 1 or professionals > 10000 or units < 0 or units > 10000:
        raise ValueError("As quantidades informadas estão fora do intervalo permitido.")
    if account_type == "INDIVIDUAL" and (professionals != 1 or units != 0):
        raise ValueError("Para profissional individual, informe 1 profissional e 0 unidades.")
    if len(password) < 12 or len(password.encode("utf-8")) > 72:
        raise ValueError("A senha deve ter ao menos 12 caracteres e no máximo 72 bytes.")
    if not data.get("confirm_data"):
        raise ValueError("Confirme que os dados cadastrais estão corretos.")

    specialties = [primary]
    for item in specialties_raw.splitlines():
        label = item.strip()
        if label and label.casefold() != primary.casefold() and label.casefold() not in {
            value.casefold() for value in specialties
        }:
            if len(label) > 100:
                raise ValueError("Cada especialidade pode ter até 100 caracteres.")
            specialties.append(label)
    if len(specialties) > 20:
        raise ValueError("Informe no máximo 20 especialidades.")

    conn = database.conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        slug = "dca-" + uuid.uuid4().hex
        organization_id = conn.execute(
            "INSERT INTO organizacoes(nome, slug) VALUES (?, ?)", (name, slug)
        ).lastrowid
        conn.execute(
            """INSERT INTO dca_organization_profiles
               (organization_id, account_type, primary_specialty, expected_professionals,
                declared_units, billing_contact_name, billing_email)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (organization_id, account_type, primary, professionals, units, contact_name, email),
        )
        for specialty in specialties:
            conn.execute(
                """INSERT INTO dca_organization_specialties
                   (organization_id, specialty, is_primary) VALUES (?, ?, ?)""",
                (organization_id, specialty, int(specialty.casefold() == primary.casefold())),
            )
        user_id = conn.execute(
            """INSERT INTO usuarios
               (nome, usuario, senha_hash, perfil, registro_profissional,
                organization_id, passkey_user_handle)
               VALUES (?, ?, ?, 'ORG_ADMIN', '', ?, ?)""",
            (
                contact_name,
                email,
                bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
                organization_id,
                secrets.token_bytes(32),
            ),
        ).lastrowid
        conn.execute(
            "INSERT INTO dca_memberships(organization_id, user_id, member_role) VALUES (?, ?, 'OWNER')",
            (organization_id, user_id),
        )
        for code, _name, _description, _route in MODULES:
            conn.execute(
                """INSERT INTO dca_organization_modules(organization_id, module_code, enabled)
                   VALUES (?, ?, 1)""",
                (organization_id, code),
            )
        plan_id = conn.execute("SELECT id FROM dca_plans WHERE code='GENERAL'").fetchone()["id"]
        subscription_id = conn.execute(
            "INSERT INTO dca_subscriptions(organization_id, plan_id, status) VALUES (?, ?, 'PENDING')",
            (organization_id, plan_id),
        ).lastrowid
        conn.execute(
            """INSERT INTO dca_subscription_events
               (subscription_id, actor_user_id, event_type, request_id)
               VALUES (?, ?, 'signup_pending', ?)""",
            (subscription_id, user_id, request_id),
        )
        conn.execute(
            """INSERT INTO dca_commercial_audit
               (organization_id, actor_user_id, action, entity_type, entity_id, request_id)
               VALUES (?, ?, 'organization_registered', 'organization', ?, ?)""",
            (organization_id, user_id, str(organization_id), request_id),
        )
        conn.commit()
        return user_id
    except database.PG_INTEGRITY_ERRORS as exc:
        conn.rollback()
        raise ValueError("Este e-mail já possui uma conta na plataforma.") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def subscription_status(organization_id: int | None) -> str | None:
    if organization_id is None:
        return None
    conn = database.conectar()
    try:
        row = conn.execute(
            "SELECT status, next_renewal_at FROM dca_subscriptions WHERE organization_id=?",
            (organization_id,),
        ).fetchone()
        if not row:
            return None
        status = row["status"]
        if status == "ACTIVE" and row["next_renewal_at"]:
            try:
                renewal = datetime.fromisoformat(str(row["next_renewal_at"]).replace("Z", "+00:00"))
                if renewal.tzinfo is None:
                    renewal = renewal.replace(tzinfo=timezone.utc)
                if renewal <= datetime.now(timezone.utc):
                    return "EXPIRED"
            except ValueError:
                return "EXPIRED"
        return status
    finally:
        conn.close()


def get_customer_dashboard(organization_id: int) -> dict:
    conn = database.conectar()
    try:
        row = conn.execute(
            """SELECT o.id, o.nome, p.account_type, p.primary_specialty,
                      p.expected_professionals, p.declared_units,
                      p.billing_contact_name, p.billing_email,
                      s.status, s.next_renewal_at, pl.name AS plan_name,
                      pl.price_cents, pl.billing_interval
               FROM organizacoes o
               JOIN dca_organization_profiles p ON p.organization_id=o.id
               JOIN dca_subscriptions s ON s.organization_id=o.id
               JOIN dca_plans pl ON pl.id=s.plan_id
               WHERE o.id=?""",
            (organization_id,),
        ).fetchone()
        if not row:
            return {}
        specialties = conn.execute(
            "SELECT specialty FROM dca_organization_specialties WHERE organization_id=? ORDER BY is_primary DESC, specialty",
            (organization_id,),
        ).fetchall()
        modules = conn.execute(
            """SELECT m.code, m.name, m.description, m.route, om.enabled
               FROM dca_organization_modules om JOIN dca_modules m ON m.code=om.module_code
               WHERE om.organization_id=? ORDER BY m.name""",
            (organization_id,),
        ).fetchall()
        user_count = conn.execute(
            "SELECT COUNT(*) AS n FROM usuarios WHERE organization_id=?",
            (organization_id,),
        ).fetchone()["n"]
        unit_count = conn.execute(
            "SELECT COUNT(*) AS n FROM dv_unidades WHERE organization_id=?",
            (organization_id,),
        ).fetchone()["n"]
        payments = conn.execute(
            """SELECT status, amount_cents, currency, confirmed_at, created_at
               FROM dca_payments WHERE subscription_id=(
                   SELECT id FROM dca_subscriptions WHERE organization_id=?)
               ORDER BY id DESC LIMIT 50""",
            (organization_id,),
        ).fetchall()
        stored_keys = conn.execute(
            "SELECT nome_stored FROM arquivos WHERE organization_id=? AND nome_stored IS NOT NULL",
            (organization_id,),
        ).fetchall()
        upload_root = (Path(__file__).resolve().parent / "uploads").resolve()
        storage_bytes = 0
        for stored in stored_keys:
            try:
                candidate = (upload_root / str(stored["nome_stored"])).resolve()
                if os.path.commonpath((str(upload_root), str(candidate))) == str(upload_root):
                    storage_bytes += candidate.stat().st_size
            except (OSError, ValueError):
                pass
        dashboard = {
            **dict(row),
            "specialties": [item["specialty"] for item in specialties],
            "modules": [dict(item) for item in modules],
            "user_count": user_count,
            "unit_count": unit_count,
            "payments": [dict(item) for item in payments],
            "storage_bytes": storage_bytes,
        }
        dashboard["status"] = subscription_status(organization_id)
        return dashboard
    finally:
        conn.close()


def list_customer_users(organization_id: int) -> list[dict]:
    conn = database.conectar()
    try:
        rows = conn.execute(
            """SELECT u.id, u.nome, u.usuario, u.perfil, u.cnes_unidade,
                      COALESCE(d.member_role, CASE WHEN u.perfil='ORG_ADMIN' THEN 'OWNER' ELSE 'MEMBER' END) AS member_role
               FROM usuarios u LEFT JOIN dca_memberships d
                 ON d.user_id=u.id AND d.organization_id=u.organization_id
               WHERE u.organization_id=? ORDER BY u.nome""",
            (organization_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def register_customer_user(
    organization_id: int, name: str, email: str, profile: str, cnes: str
) -> int:
    name, email, profile, cnes = (str(x or "").strip() for x in (name, email, profile, cnes))
    if not name or len(name) > 160 or len(email) > 80 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("Informe nome e e-mail válidos.")
    if profile.upper() not in {"DENTISTA", "MEDICO", "MÉDICO", "ENFERMEIRO", "ENFERMEIRA", "PSICOLOGO", "PSICÓLOGO", "FARMACIA", "FARMÁCIA", "FARMACEUTICO", "FARMACÊUTICO", "RECEPCAO", "RECEPÇÃO"}:
        raise ValueError("Perfil profissional inválido.")
    conn = database.conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        unit = conn.execute(
            "SELECT 1 FROM dv_unidades WHERE cnes=? AND organization_id=?",
            (cnes, organization_id),
        ).fetchone()
        if not unit:
            raise ValueError("Selecione uma unidade CNES vinculada à sua organização.")
        user_id = conn.execute(
            """INSERT INTO usuarios
               (nome, usuario, senha_hash, perfil, registro_profissional, cnes_unidade,
                organization_id, passkey_user_handle)
               VALUES (?, ?, '', ?, '', ?, ?, ?)""",
            (name, email.lower(), profile.upper(), cnes, organization_id, secrets.token_bytes(32)),
        ).lastrowid
        conn.execute(
            "INSERT INTO dca_memberships(organization_id, user_id, member_role) VALUES (?, ?, 'MEMBER')",
            (organization_id, user_id),
        )
        conn.commit()
        return user_id
    except database.PG_INTEGRITY_ERRORS as exc:
        conn.rollback()
        raise ValueError("Este e-mail já possui uma conta na plataforma.") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def change_customer_user_profile(
    organization_id: int, user_id: int, profile: str, actor_user_id: int,
    request_id: str | None,
) -> bool:
    profile = str(profile or "").strip().upper()
    if profile not in MEMBER_PROFILES:
        return False
    conn = database.conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        actor = conn.execute(
            "SELECT perfil, organization_id FROM usuarios WHERE id=?", (actor_user_id,)
        ).fetchone()
        row = conn.execute(
            "SELECT perfil FROM usuarios WHERE id=? AND organization_id=?",
            (user_id, organization_id),
        ).fetchone()
        allowed_actor = bool(
            actor and (
                actor["perfil"] == "DCA_SUPER_ADMIN"
                or (actor["perfil"] == "ORG_ADMIN" and actor["organization_id"] == organization_id)
            )
        )
        if not allowed_actor or not row or row["perfil"] == "ORG_ADMIN":
            conn.rollback()
            return False
        conn.execute(
            "UPDATE usuarios SET perfil=?, session_version=session_version+1 WHERE id=? AND organization_id=?",
            (profile, user_id, organization_id),
        )
        conn.execute(
            """INSERT INTO dca_commercial_audit
               (organization_id, actor_user_id, action, entity_type, entity_id, request_id)
               VALUES (?, ?, 'member_profile_changed', 'user', ?, ?)""",
            (organization_id, actor_user_id, str(user_id), request_id),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_super_admin_organizations() -> list[dict]:
    conn = database.conectar()
    try:
        rows = conn.execute(
            """SELECT o.id, s.id AS subscription_id, o.nome, p.account_type, p.billing_contact_name,
                      p.billing_email, p.expected_professionals, p.declared_units,
                      s.status, s.created_at,
                      (SELECT COUNT(*) FROM usuarios u WHERE u.organization_id=o.id) AS users,
                      (SELECT COUNT(*) FROM dv_unidades u WHERE u.organization_id=o.id) AS units
               FROM organizacoes o
               LEFT JOIN dca_organization_profiles p ON p.organization_id=o.id
               LEFT JOIN dca_subscriptions s ON s.organization_id=o.id
               WHERE p.organization_id IS NOT NULL ORDER BY s.created_at DESC"""
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def list_super_admin_users() -> list[dict]:
    """Return organization account metadata only; never query clinical tables."""
    conn = database.conectar()
    try:
        rows = conn.execute(
            """SELECT o.id AS organization_id, o.nome AS organization_name,
                      u.id, u.nome, u.usuario, u.perfil, u.cnes_unidade
               FROM usuarios u
               JOIN organizacoes o ON o.id=u.organization_id
               JOIN dca_organization_profiles p ON p.organization_id=o.id
               ORDER BY o.nome, u.nome"""
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def mark_subscription_review(subscription_id: int, actor_user_id: int, request_id: str | None) -> bool:
    conn = database.conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        actor = conn.execute(
            "SELECT perfil FROM usuarios WHERE id=?", (actor_user_id,)
        ).fetchone()
        if not actor or actor["perfil"] != "DCA_SUPER_ADMIN":
            conn.rollback()
            return False
        row = conn.execute(
            "SELECT id, organization_id, status FROM dca_subscriptions WHERE id=?",
            (subscription_id,),
        ).fetchone()
        if not row or row["status"] != "PENDING":
            conn.rollback()
            return False
        conn.execute(
            "UPDATE dca_subscriptions SET status='UNDER_REVIEW', updated_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), subscription_id),
        )
        conn.execute(
            """INSERT INTO dca_subscription_events
               (subscription_id, actor_user_id, event_type, request_id)
               VALUES (?, ?, 'review_started', ?)""",
            (subscription_id, actor_user_id, request_id),
        )
        conn.execute(
            """INSERT INTO dca_commercial_audit
               (organization_id, actor_user_id, action, entity_type, entity_id, request_id)
               VALUES (?, ?, 'subscription_review_started', 'subscription', ?, ?)""",
            (row["organization_id"], actor_user_id, str(subscription_id), request_id),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_organization_modules(organization_id: int) -> list[dict]:
    conn = database.conectar()
    try:
        rows = conn.execute(
            """SELECT m.code, m.name, om.enabled FROM dca_organization_modules om
               JOIN dca_modules m ON m.code=om.module_code
               WHERE om.organization_id=? ORDER BY m.name""",
            (organization_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def set_organization_module(organization_id: int, module_code: str, enabled: bool, actor_user_id: int, request_id: str | None) -> bool:
    conn = database.conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        actor = conn.execute(
            "SELECT perfil FROM usuarios WHERE id=?", (actor_user_id,)
        ).fetchone()
        if not actor or actor["perfil"] != "DCA_SUPER_ADMIN":
            conn.rollback()
            return False
        row = conn.execute(
            "SELECT id FROM dca_subscriptions WHERE organization_id=?",
            (organization_id,),
        ).fetchone()
        module = conn.execute(
            "SELECT 1 FROM dca_modules WHERE code=?", (module_code,)
        ).fetchone()
        if not row or not module:
            conn.rollback()
            return False
        conn.execute(
            """UPDATE dca_organization_modules SET enabled=?, changed_by=?, updated_at=?
               WHERE organization_id=? AND module_code=?""",
            (int(enabled), actor_user_id, datetime.now(timezone.utc).isoformat(), organization_id, module_code),
        )
        conn.execute(
            """INSERT INTO dca_commercial_audit
               (organization_id, actor_user_id, action, entity_type, entity_id, request_id)
               VALUES (?, ?, ?, 'module', ?, ?)""",
            (organization_id, actor_user_id, "module_enabled" if enabled else "module_disabled", module_code, request_id),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def module_enabled(organization_id: int, module_code: str) -> bool:
    if organization_id is None:
        return False
    conn = database.conectar()
    try:
        row = conn.execute(
            """SELECT om.enabled, s.status FROM dca_organization_modules om
               JOIN dca_subscriptions s ON s.organization_id=om.organization_id
               WHERE om.organization_id=? AND om.module_code=?""",
            (organization_id, module_code),
        ).fetchone()
        return bool(row and row["enabled"] and row["status"] == "ACTIVE")
    finally:
        conn.close()


def _csrf_token() -> str:
    token = session.get("_auth_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_auth_csrf"] = token
    return token


def _valid_csrf() -> bool:
    token = session.get("_auth_csrf", "")
    supplied = request.form.get("csrf_token", "")
    return bool(token and supplied and secrets.compare_digest(token, supplied))


def _preauth_user():
    user_id = session.get("dca_mfa_pending_user_id")
    if not user_id or session.get("dca_mfa_pending_version") is None:
        return None
    user = database.buscar_usuario_por_id(user_id)
    if (not user or user.get("perfil") != "DCA_SUPER_ADMIN"
            or user.get("session_version") != session.get("dca_mfa_pending_version")):
        session.clear()
        return None
    return user


def _finish_mfa_login(user):
    next_path = session.get("dca_mfa_next", "/dca/admin")
    if not isinstance(next_path, str) or not next_path.startswith("/") or next_path.startswith("//"):
        next_path = "/dca/admin"
    recovery_codes = session.pop("dca_recovery_codes_after_login", None)
    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["user_nome"] = user["nome"]
    session["user_perfil"] = user["perfil"]
    session["cnes_unidade"] = user.get("cnes_unidade") or ""
    session["auth_version"] = user["session_version"]
    session["organization_id"] = user.get("organization_id")
    session["dca_mfa_authenticated"] = True
    if recovery_codes:
        session["dca_recovery_codes_once"] = recovery_codes
    database.registrar_auditoria(
        user["id"], "dca_mfa_login_success", "auth", user["id"],
        request.environ.get("dv_request_id"),
    )
    if recovery_codes:
        return redirect(url_for("commercial.dca_mfa_recovery_codes"))
    return redirect(next_path)


@commercial_bp.route("/dca/mfa/setup", methods=["GET", "POST"])
def dca_mfa_setup():
    user = _preauth_user()
    if not user:
        return redirect(url_for("login"))
    if mfa_enabled(user["id"]):
        return redirect(url_for("commercial.dca_mfa_verify"))
    try:
        secret = begin_mfa_setup(user["id"])
    except (PermissionError, ValueError):
        return redirect(url_for("login"))

    message = ""
    if request.method == "POST":
        if not _valid_csrf():
            abort(400, description="Token CSRF invÃ¡lido.")
        recovery_codes = activate_mfa(user["id"], request.form.get("code", ""))
        if recovery_codes:
            session["dca_recovery_codes_after_login"] = recovery_codes
            return _finish_mfa_login(user)
        message = "CÃ³digo invÃ¡lido ou tentativas temporariamente bloqueadas. Tente novamente mais tarde."

    uri = pyotp.TOTP(secret).provisioning_uri(
        name=user["usuario"], issuer_name="DCA Software â€” Super Admin"
    )
    image_buffer = io.BytesIO()
    qrcode.make(uri).save(image_buffer, format="PNG")
    qr_data = base64.b64encode(image_buffer.getvalue()).decode("ascii")
    content = f"""<section class="hero"><h1>Proteja a conta Super Admin</h1>
      <p>O uso do painel administrativo exige um aplicativo autenticador TOTP.</p></section>
      {f'<p class="error" role="alert">{escape(message)}</p>' if message else ''}
      <section class="panel"><p>Escaneie este QR Code em um aplicativo autenticador. Se necessÃ¡rio, insira a chave manualmente:</p>
      <img width="220" height="220" alt="QR Code para configurar MFA" src="data:image/png;base64,{qr_data}">
      <p><code>{escape(secret)}</code></p>
      <form method="post"><input type="hidden" name="csrf_token" value="{escape(_csrf_token())}">
      <label>CÃ³digo de seis dÃ­gitos<input name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{{6}}" maxlength="6" required></label>
      <button type="submit">Ativar MFA e concluir acesso</button></form></section>"""
    return _page("ConfiguraÃ§Ã£o MFA â€” DCA Software", content)


@commercial_bp.route("/dca/mfa/verify", methods=["GET", "POST"])
def dca_mfa_verify():
    user = _preauth_user()
    if not user:
        return redirect(url_for("login"))
    if not mfa_enabled(user["id"]):
        return redirect(url_for("commercial.dca_mfa_setup"))
    message = ""
    if request.method == "POST":
        if not _valid_csrf():
            abort(400, description="Token CSRF invÃ¡lido.")
        if verify_mfa(user["id"], request.form.get("code", "")):
            return _finish_mfa_login(user)
        message = "CÃ³digo invÃ¡lido ou tentativas temporariamente bloqueadas. Use um cÃ³digo de recuperaÃ§Ã£o, se necessÃ¡rio."
    content = f"""<section class="hero"><h1>VerificaÃ§Ã£o em duas etapas</h1>
      <p>Digite o cÃ³digo atual do aplicativo autenticador ou um cÃ³digo de recuperaÃ§Ã£o nÃ£o utilizado.</p></section>
      {f'<p class="error" role="alert">{escape(message)}</p>' if message else ''}
      <form class="panel" method="post"><input type="hidden" name="csrf_token" value="{escape(_csrf_token())}">
      <label>CÃ³digo<input name="code" autocomplete="one-time-code" maxlength="16" required autofocus></label>
      <button type="submit">Verificar e entrar</button></form>"""
    return _page("VerificaÃ§Ã£o MFA â€” DCA Software", content)


@commercial_bp.get("/dca/mfa/recovery-codes")
def dca_mfa_recovery_codes():
    if session.get("user_perfil") != "DCA_SUPER_ADMIN" or not session.get("dca_mfa_authenticated"):
        return redirect(url_for("login"))
    codes = session.pop("dca_recovery_codes_once", [])
    if not codes:
        return redirect(url_for("commercial.super_admin_dashboard"))
    code_rows = "\n".join(escape(code) for code in codes)
    content = ("<section class=\"hero\"><h1>CÃ³digos de recuperaÃ§Ã£o</h1>"
               "<p>Guarde-os em local seguro. Cada cÃ³digo funciona uma Ãºnica vez e nÃ£o poderÃ¡ ser exibido novamente.</p></section>"
               f"<pre class=\"panel\" aria-label=\"CÃ³digos de recuperaÃ§Ã£o\">{code_rows}</pre>"
               "<p><a class=\"button\" href=\"/dca/admin\">Ir para o painel</a></p>")
    return _page("CÃ³digos de recuperaÃ§Ã£o â€” DCA Software", content)


def _page(title: str, content: str) -> str:
    admin_link = (
        '<a href="/dca/admin">Super Admin DCA</a> &nbsp;'
        if session.get("user_perfil") == "DCA_SUPER_ADMIN" else ""
    )
    return render_template_string(
        """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{{ title }}</title><style>
        *{box-sizing:border-box}body{margin:0;background:#f3f6fb;color:#10243b;font:16px system-ui,sans-serif}
        header{background:#08213c;color:#fff;padding:14px max(18px,calc((100% - 1120px)/2));display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
        header a{color:#fff;text-decoration:none}main{max-width:1120px;margin:28px auto;padding:0 16px}
        .hero,.panel,.card{background:#fff;border:1px solid #dbe4ef;border-radius:14px;padding:22px;margin:14px 0}
        .hero{background:linear-gradient(120deg,#e8f1ff,#fff)}h1{margin:4px 0 10px}p{line-height:1.55;color:#53657b}
        .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:12px}
        .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,230px),1fr));gap:12px}
        .card{margin:0}.button,button{display:inline-block;background:#1459a6;color:#fff;border:0;border-radius:8px;padding:11px 15px;text-decoration:none;font:inherit;cursor:pointer}
        input,select,textarea{width:100%;padding:10px;border:1px solid #bdc9d8;border-radius:8px;font:inherit;margin:5px 0 12px}
        label{display:block;font-weight:600;font-size:14px}table{width:100%;border-collapse:collapse;background:#fff}th,td{text-align:left;padding:10px;border-bottom:1px solid #e3e9f0;vertical-align:top}
        .table-wrap{overflow:auto}.notice{padding:12px;background:#fff7df;border:1px solid #f0d68b;border-radius:8px}.error{color:#a31924}
        .muted{color:#65768a;font-size:14px}.pill{display:inline-block;border-radius:99px;background:#fff0c8;padding:4px 10px;font-size:13px}
        </style></head><body><header><a href="/dca/"><strong>DCA SOFTWARE</strong></a>
        <nav>{{ admin_link|safe }}<a href="/cliente/">Minha organização</a> &nbsp; <a href="/minha-conta">Minha conta</a> &nbsp;
        <a href="/login">Entrar</a></nav></header><main>{{ content|safe }}</main></body></html>""",
        title=title,
        content=content,
        admin_link=admin_link,
    )


@commercial_bp.get("/dca/")
def dca_portal():
    content = """
      <section class="hero"><p>DCA SOFTWARE — PLATAFORMA INTEGRADA</p>
      <h1>Uma plataforma. Um plano geral.</h1>
      <p>Conheça o acesso integrado a Médico, Odonto, Farma, Gestão, Prontuário, Estoque e Relatórios.
      A disponibilidade comercial e os valores serão informados após definição das condições de contratação.</p>
      <a class="button" href="/dca/cadastro">Solicitar cadastro</a>
      <a class="button" href="/login">Entrar</a></section>
      <section class="cards"><article class="card"><h2>Plano Geral</h2><p>Uma organização e uma assinatura principal, com os módulos da plataforma.</p></article>
      <article class="card"><h2>Profissional ou estabelecimento</h2><p>Cadastro de várias especialidades, profissionais e unidades.</p></article>
      <article class="card"><h2>Assinatura pendente</h2><p>O cadastro não gera cobrança nem ativa acesso clínico. Não há gateway conectado.</p></article></section>
    """
    return _page("DCA Software", content)


@commercial_bp.route("/dca/cadastro", methods=["GET", "POST"])
def customer_register():
    message = ""
    if request.method == "POST":
        if not _valid_csrf():
            abort(400, description="Token de segurança inválido.")
        try:
            user_id = register_customer(request.form.to_dict(), request.environ.get("dv_request_id"))
        except ValueError as exc:
            message = str(exc)
        else:
            user = database.buscar_usuario_por_id(user_id)
            session.clear()
            session.permanent = True
            session["user_id"] = user["id"]
            session["user_nome"] = user["nome"]
            session["user_perfil"] = user["perfil"]
            session["cnes_unidade"] = ""
            session["organization_id"] = user["organization_id"]
            session["auth_version"] = user["session_version"]
            return redirect(url_for("commercial.customer_dashboard"))
    content = f"""
      <section class="hero"><h1>Cadastro da organização</h1>
      <p>O cadastro cria uma assinatura pendente. Não há cobrança ou ativação automática.</p></section>
      {f'<p class="error" role="alert">{escape(message)}</p>' if message else ''}
      <form class="panel" method="post"><input type="hidden" name="csrf_token" value="{escape(_csrf_token())}">
        <label>Tipo de cliente<select name="account_type" required><option value="">Selecione</option><option value="INDIVIDUAL">Profissional individual</option><option value="ESTABLISHMENT">Estabelecimento</option></select></label>
        <label>Nome da organização ou do profissional<input name="organization_name" maxlength="160" required></label>
        <label>Responsável financeiro<input name="contact_name" maxlength="160" autocomplete="name" required></label>
        <label>E-mail de acesso e cobrança<input name="email" type="email" maxlength="80" autocomplete="email" required></label>
        <label>Especialidade principal<input name="primary_specialty" maxlength="100" required></label>
        <label>Outras especialidades (uma por linha)<textarea name="specialties" maxlength="2000" rows="4"></textarea></label>
        <div class="grid"><label>Profissionais<input type="number" name="professional_count" min="1" max="10000" value="1" required></label>
        <label>Unidades<input type="number" name="unit_count" min="0" max="10000" value="0" required></label></div>
        <label>Senha (mínimo 12 caracteres)<input type="password" name="password" minlength="12" maxlength="72" autocomplete="new-password" required></label>
        <label><input type="checkbox" name="confirm_data" value="yes" required style="width:auto"> Confirmo que os dados cadastrais informados estão corretos.</label>
        <button type="submit">Criar cadastro pendente</button>
      </form>
    """
    return _page("Cadastro — DCA Software", content)


def _require_customer_owner():
    if not session.get("user_id"):
        return redirect(url_for("login", next=request.url))
    if session.get("user_perfil") != "ORG_ADMIN" or not session.get("organization_id"):
        abort(403)
    return None


@commercial_bp.get("/cliente/")
def customer_dashboard():
    denied = _require_customer_owner()
    if denied:
        return denied
    data = get_customer_dashboard(int(session["organization_id"]))
    if not data:
        abort(404)
    status = {
        "PENDING": "Pendente de confirmação",
        "UNDER_REVIEW": "Em análise DCA",
        "ACTIVE": "Ativa",
        "PAST_DUE": "Em atraso",
        "CANCELLED": "Cancelada",
        "SUSPENDED": "Suspensa",
    }.get(data["status"], "Estado não reconhecido")
    module_cards = "".join(
        f'<article class="card"><h2>{escape(module["name"])}</h2><p>{escape(module["description"])}</p><span class="pill">{"Aguardando ativação" if module["enabled"] else "Desabilitado pela DCA"}</span></article>'
        for module in data["modules"]
    )
    payment_rows = "".join(
        f'<tr><td>{escape(payment["created_at"])}</td><td>{escape(payment["status"])}</td><td>{(payment["amount_cents"] / 100):.2f} {escape(payment["currency"] or "") if payment["amount_cents"] is not None else "—"}</td></tr>'
        for payment in data["payments"]
    ) or '<tr><td colspan="3">Nenhum pagamento registrado. O gateway ainda não está conectado.</td></tr>'
    content = f"""
      <section class="hero"><p>PAINEL DO CLIENTE</p><h1>{escape(data['nome'])}</h1>
      <p>Plano: <strong>{escape(data['plan_name'])}</strong> &nbsp; Situação: <span class="pill">{status}</span></p>
      <p>Preço e periodicidade: ainda não definidos.</p>
      <p class="notice">O plano permanece pendente até uma futura integração de gateway confirmar a contratação. Nenhum pagamento foi simulado.</p></section>
      <section class="grid"><article class="panel"><strong>Usuários</strong><p>{data['user_count']} cadastrados · previsão {data['expected_professionals']}</p></article>
      <article class="panel"><strong>Especialidades</strong><p>{escape(', '.join(data['specialties']))}</p></article>
      <article class="panel"><strong>Unidades</strong><p>{data['unit_count']} cadastradas · previsão {data['declared_units']}</p></article>
      <article class="panel"><strong>Armazenamento</strong><p>{data['storage_bytes'] / (1024 * 1024):.2f} MB em documentos registrados</p></article>
      <article class="panel"><strong>Próxima renovação</strong><p>{escape(data['next_renewal_at'] or 'Não definida enquanto a assinatura estiver pendente')}</p></article>
      <article class="panel"><strong>Responsável financeiro</strong><p>{escape(data['billing_contact_name'])}<br>{escape(data['billing_email'])}</p></article></section>
      <p><a class="button" href="/cliente/usuarios">Usuários e permissões</a>
      <a class="button" href="/cliente/unidades">Unidades da organização</a></p>
      <h2>Módulos do Plano Geral</h2><section class="cards">{module_cards}</section>
      <h2>Histórico de pagamentos</h2><div class="table-wrap"><table><thead><tr><th>Data</th><th>Situação</th><th>Valor e moeda</th></tr></thead><tbody>{payment_rows}</tbody></table></div>
    """
    return _page("Minha organização — DCA Software", content)


@commercial_bp.get("/cliente/acesso-pendente")
def customer_access_pending():
    if not session.get("user_id") or not session.get("organization_id"):
        return redirect(url_for("login", next=request.url))
    status = subscription_status(int(session["organization_id"]))
    if status is None or status == "ACTIVE":
        return redirect(url_for("index"))
    state_labels = {
        "PENDING": "pendente de confirmação",
        "UNDER_REVIEW": "em análise pela DCA",
        "PAST_DUE": "com pagamento atrasado",
        "SUSPENDED": "suspensa",
        "CANCELLED": "cancelada",
        "EXPIRED": "expirada",
    }
    label = state_labels.get(status, "pendente de confirmação")
    content = f"""<section class="hero"><p>ACESSO À PLATAFORMA</p><h1>Assinatura {escape(label.lower())}</h1>
      <p>O acesso aos módulos profissionais será liberado somente após confirmação válida da contratação. Nenhum pagamento foi simulado.</p>
      <p>Seu cadastro e os dados da organização continuam preservados. Procure o responsável da organização para acompanhar a solicitação.</p>
      <a class="button" href="/dca/">Portal DCA Software</a></section>"""
    return _page("Acesso pendente — DCA Software", content)


@commercial_bp.route("/cliente/unidades", methods=["GET", "POST"])
def customer_units():
    denied = _require_customer_owner()
    if denied:
        return denied
    org_id = int(session["organization_id"])
    message = ""
    if request.method == "POST":
        if not _valid_csrf():
            abort(400, description="Token de segurança inválido.")
        cnes = request.form.get("cnes", "").strip()
        name = request.form.get("name", "").strip()
        if len(cnes) != 7 or not cnes.isdigit() or not name or len(name) > 160:
            message = "Informe um CNES de 7 dígitos e o nome da unidade."
        else:
            conn = database.conectar()
            try:
                conn.execute(
                    "INSERT INTO dv_unidades(cnes, nome, organization_id) VALUES (?, ?, ?)",
                    (cnes, name, org_id),
                )
                conn.execute(
                    """INSERT INTO dca_commercial_audit
                       (organization_id, actor_user_id, action, entity_type, entity_id, request_id)
                       VALUES (?, ?, 'unit_registered', 'unit', ?, ?)""",
                    (org_id, session["user_id"], cnes, request.environ.get("dv_request_id")),
                )
                conn.commit()
                return redirect(url_for("commercial.customer_units"))
            except database.PG_INTEGRITY_ERRORS:
                conn.rollback()
                message = "Esse CNES já está cadastrado. A associação com uma organização existente requer análise da DCA; nenhum dado foi movido."
            finally:
                conn.close()
    conn = database.conectar()
    try:
        units = conn.execute(
            "SELECT cnes, nome FROM dv_unidades WHERE organization_id=? ORDER BY nome", (org_id,)
        ).fetchall()
    finally:
        conn.close()
    rows = "".join(f"<tr><td>{escape(row['nome'])}</td><td>{escape(row['cnes'])}</td></tr>" for row in units)
    content = f"""<section class="hero"><h1>Unidades</h1><p>O CNES permanece vinculado a esta organização.</p></section>
      {f'<p class="error">{escape(message)}</p>' if message else ''}
      <form class="panel" method="post"><input type="hidden" name="csrf_token" value="{escape(_csrf_token())}">
      <label>Nome da unidade<input name="name" maxlength="160" required></label>
      <label>CNES (7 dígitos)<input name="cnes" inputmode="numeric" pattern="[0-9]{{7}}" maxlength="7" required></label>
      <button type="submit">Cadastrar unidade</button></form>
      <div class="table-wrap"><table><thead><tr><th>Unidade</th><th>CNES</th></tr></thead><tbody>{rows or '<tr><td colspan="2">Nenhuma unidade cadastrada.</td></tr>'}</tbody></table></div>"""
    return _page("Unidades — DCA Software", content)


@commercial_bp.route("/cliente/usuarios", methods=["GET", "POST"])
def customer_users():
    denied = _require_customer_owner()
    if denied:
        return denied
    org_id = int(session["organization_id"])
    invite = ""
    message = ""
    if request.method == "POST":
        if not _valid_csrf():
            abort(400, description="Token de segurança inválido.")
        if request.form.get("action") == "change_profile":
            try:
                target_id = int(request.form.get("user_id", ""))
            except ValueError:
                message = "Usuário inválido."
            else:
                changed = change_customer_user_profile(
                    org_id, target_id, request.form.get("profile", ""),
                    session["user_id"], request.environ.get("dv_request_id"),
                )
                message = "Permissão atualizada; a sessão anterior foi invalidada." if changed else "Não foi possível alterar este perfil."
        else:
            try:
                user_id = register_customer_user(
                    org_id, request.form.get("name", ""), request.form.get("email", ""),
                    request.form.get("profile", ""), request.form.get("cnes", ""),
                )
                invite = database.criar_convite_passkey(user_id) or ""
                if not invite:
                    message = "Conta criada, mas não foi possível gerar o convite de acesso."
            except ValueError as exc:
                message = str(exc)
    users = list_customer_users(org_id)
    conn = database.conectar()
    try:
        units = conn.execute(
            "SELECT cnes, nome FROM dv_unidades WHERE organization_id=? ORDER BY nome", (org_id,)
        ).fetchall()
    finally:
        conn.close()
    profile_options = "".join(
        f'<option value="{profile}">{profile}</option>'
        for profile in MEMBER_PROFILES
    )
    unit_options = "".join(
        f'<option value="{escape(unit["cnes"])}">{escape(unit["nome"])} — CNES {escape(unit["cnes"])}</option>'
        for unit in units
    )
    rows = "".join(
        f"<tr><td>{escape(user['nome'])}</td><td>{escape(user['usuario'])}</td><td>{escape(user['perfil'])}</td><td>{escape(user['cnes_unidade'] or '—')}</td><td>{escape(user['member_role'])}</td><td>"
        + (f'<form method="post"><input type="hidden" name="csrf_token" value="{escape(_csrf_token())}"><input type="hidden" name="action" value="change_profile"><input type="hidden" name="user_id" value="{user["id"]}"><select name="profile">{profile_options}</select><button type="submit">Alterar perfil</button></form>' if user["perfil"] != "ORG_ADMIN" else "Responsável")
        + "</td></tr>"
        for user in users
    )
    content = f"""<section class="hero"><h1>Usuários e permissões</h1><p>Contas profissionais ficam associadas a um CNES da organização. O convite de acesso é de uso único.</p></section>
      {f'<p class="error">{escape(message)}</p>' if message else ''}
      {f'<p class="notice">Convite de uso único; compartilhe com a pessoa por canal confiável: <code>{escape(invite)}</code></p>' if invite else ''}
      <form class="panel" method="post"><input type="hidden" name="csrf_token" value="{escape(_csrf_token())}">
      <label>Nome<input name="name" maxlength="160" required></label><label>E-mail<input name="email" type="email" maxlength="80" required></label>
      <label>Perfil profissional<select name="profile" required>{profile_options}</select></label>
      <label>Unidade CNES<select name="cnes" required><option value="">Selecione</option>{unit_options}</select></label>
      <button type="submit">Criar usuário e emitir convite</button></form>
      <div class="table-wrap"><table><thead><tr><th>Nome</th><th>Login</th><th>Perfil</th><th>CNES</th><th>Vínculo</th><th>Permissão</th></tr></thead><tbody>{rows or '<tr><td colspan="6">Nenhum usuário profissional cadastrado.</td></tr>'}</tbody></table></div>"""
    response = make_response(_page("Usuários — DCA Software", content))
    if invite:
        response.headers["Cache-Control"] = "no-store"
    return response


@commercial_bp.route("/dca/admin", methods=["GET", "POST"])
def super_admin_dashboard():
    if not session.get("user_id"):
        return redirect(url_for("login", next=request.url))
    if session.get("user_perfil") != "DCA_SUPER_ADMIN":
        abort(403)
    message = ""
    if request.method == "POST":
        if not _valid_csrf():
            abort(400, description="Token de segurança inválido.")
        action = request.form.get("action", "review")
        if action == "toggle_module":
            try:
                org_id = int(request.form.get("organization_id", ""))
            except ValueError:
                message = "Organização inválida."
            else:
                if not set_organization_module(
                    org_id, request.form.get("module_code", ""),
                    request.form.get("enabled") == "1", session["user_id"],
                    request.environ.get("dv_request_id"),
                ):
                    message = "Não foi possível alterar o módulo."
                else:
                    message = "Configuração do módulo salva; a assinatura continua no estado atual."
        elif action == "change_user_profile":
            try:
                org_id = int(request.form.get("organization_id", ""))
                user_id = int(request.form.get("user_id", ""))
            except ValueError:
                message = "Organização ou usuário inválido."
            else:
                if not change_customer_user_profile(
                    org_id, user_id, request.form.get("profile", ""),
                    session["user_id"], request.environ.get("dv_request_id"),
                ):
                    message = "Não foi possível alterar este perfil."
                else:
                    message = "Permissão atualizada; a sessão anterior foi invalidada."
        else:
            try:
                subscription_id = int(request.form.get("subscription_id", ""))
            except ValueError:
                message = "Assinatura inválida."
            else:
                if not mark_subscription_review(subscription_id, session["user_id"], request.environ.get("dv_request_id")):
                    message = "A solicitação não pôde ser movida para análise."
                else:
                    message = "Solicitação encaminhada para análise. Nenhuma assinatura foi ativada."
    organizations = list_super_admin_organizations()
    users = list_super_admin_users()
    org_rows = "".join(
        f"<tr><td>{escape(org['nome'])}</td><td>{escape(org['account_type'] or '—')}</td><td>{escape(org['billing_contact_name'] or '—')}<br>{escape(org['billing_email'] or '—')}</td><td>{org['users']}</td><td>{org['units']}/{org['declared_units'] if org['declared_units'] is not None else '—'}</td><td>{escape(org['status'] or '—')}</td><td>"
        + (f'<form method="post"><input type="hidden" name="csrf_token" value="{escape(_csrf_token())}"><input type="hidden" name="subscription_id" value="{org["subscription_id"]}"><button type="submit">Iniciar análise</button></form>' if org["status"] == "PENDING" else "—")
        + "</td><td><ul>" + "".join(
            f'<li>{escape(module["name"])} — {"habilitado" if module["enabled"] else "desabilitado"} '
            f'<form style="display:inline" method="post"><input type="hidden" name="csrf_token" value="{escape(_csrf_token())}">'
            f'<input type="hidden" name="action" value="toggle_module"><input type="hidden" name="organization_id" value="{org["id"]}">'
            f'<input type="hidden" name="module_code" value="{escape(module["code"])}"><input type="hidden" name="enabled" value="{0 if module["enabled"] else 1}">'
            f'<button type="submit">{"Desabilitar" if module["enabled"] else "Habilitar"}</button></form></li>'
            for module in list_organization_modules(org["id"])
        ) + "</ul></td></tr>"
        for org in organizations
    ) or '<tr><td colspan="8">Nenhuma organização DCA registrada.</td></tr>'
    user_rows = "".join(
        f"<tr><td>{escape(user['organization_name'])}</td><td>{escape(user['nome'])}</td><td>{escape(user['usuario'])}</td><td>{escape(user['perfil'])}</td><td>"
        + (f'<form method="post"><input type="hidden" name="csrf_token" value="{escape(_csrf_token())}"><input type="hidden" name="action" value="change_user_profile"><input type="hidden" name="organization_id" value="{user["organization_id"]}"><input type="hidden" name="user_id" value="{user["id"]}"><select name="profile">{"".join(f"<option value=\"{p}\">{p}</option>" for p in MEMBER_PROFILES)}</select><button type="submit">Alterar perfil</button></form>' if user["perfil"] != "ORG_ADMIN" else "Responsável")
        + "</td></tr>"
        for user in users
    ) or '<tr><td colspan="5">Nenhum usuário comercial registrado.</td></tr>'
    module_rows = "".join(f"<li><strong>{escape(name)}</strong> — {escape(description)}</li>" for _code, name, description, _route in MODULES)
    content = f"""<section class="hero"><p>ADMINISTRAÇÃO COMERCIAL DCA</p><h1>Super Admin</h1>
      <p>Acesso a metadados comerciais. Esta área não consulta prontuários ou dados de pacientes.</p>
      <p class="notice">Não há gateway, preços ou limites comerciais configurados. Não é possível ativar assinaturas neste painel.</p></section>
      {f'<p class="notice" role="status">{escape(message)}</p>' if message else ''}
      <h2>Plano Geral e módulos</h2><section class="panel"><h3>DCA SOFTWARE — PLANO GERAL</h3><ul>{module_rows}</ul><p>Preço e periodicidade não definidos.</p></section>
      <h2>Organizações, assinaturas e módulos</h2><div class="table-wrap"><table><thead><tr><th>Organização</th><th>Tipo</th><th>Responsável financeiro</th><th>Usuários</th><th>Unidades</th><th>Assinatura</th><th>Análise</th><th>Módulos</th></tr></thead><tbody>{org_rows}</tbody></table></div>
      <h2>Usuários das organizações</h2><div class="table-wrap"><table><thead><tr><th>Organização</th><th>Nome</th><th>Login</th><th>Perfil</th><th>Permissão</th></tr></thead><tbody>{user_rows}</tbody></table></div>
    """
    return _page("Super Admin — DCA Software", content)

