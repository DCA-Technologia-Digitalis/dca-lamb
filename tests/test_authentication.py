import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from pathlib import Path

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))
_isolated_import_dir = tempfile.TemporaryDirectory(prefix="dv-auth-import-")
_original_cwd = os.getcwd()
_original_database_path = os.environ.get("DV_DATABASE_PATH")
os.environ["DV_DATABASE_PATH"] = str(
    Path(_isolated_import_dir.name) / "dv_odonto.db"
)
os.chdir(_isolated_import_dir.name)
try:
    import app as app_module
    import database
    import dv_saude
finally:
    os.chdir(_original_cwd)
    if _original_database_path is None:
        os.environ.pop("DV_DATABASE_PATH", None)
    else:
        os.environ["DV_DATABASE_PATH"] = _original_database_path


@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    db_path = tmp_path / "auth_test.db"
    monkeypatch.setattr(database, "DB_NAME", str(db_path))
    database.criar_tabelas()
    dv_saude._ensure_schema()
    conn = database.conectar()
    try:
        conn.execute(
            "INSERT INTO dv_unidades (cnes, nome) VALUES (?, ?)",
            ("1234567", "Unidade de teste"),
        )
        conn.commit()
    finally:
        conn.close()
    database.cadastrar_usuario(
        "Administrador de teste",
        "admin",
        "secure-admin-password",
        "ADMIN",
    )
    app_module.app.config.update(
        TESTING=True,
        SECRET_KEY="test-only-session-key",
        ADMIN_RECOVERY_BACKUP_DIR=str(tmp_path / "backups"),
    )
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session.clear()
    return client


def _csrf_token(response):
    match = re.search(
        r'name="csrf_token" value="([^"]+)"',
        response.get_data(as_text=True),
    )
    assert match
    return match.group(1)


def _login(client, username, password):
    page = client.get("/login")
    return client.post(
        "/login",
        data={
            "csrf_token": _csrf_token(page),
            "usuario": username,
            "senha": password,
        },
    )


def test_legacy_database_migration_preserves_patient_and_dental_records(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    monkeypatch.setattr(database, "DB_NAME", str(db_path))
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE pacientes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                telefone TEXT,
                cpf TEXT,
                cns TEXT,
                data_nascimento TEXT,
                sexo TEXT,
                bairro TEXT,
                peso REAL,
                altura REAL,
                pressao TEXT,
                imc REAL,
                classificacao_imc TEXT
            );
            CREATE TABLE odontograma (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paciente_id INTEGER,
                dente TEXT,
                face TEXT,
                condicao TEXT,
                codigo_sigtap TEXT,
                FOREIGN KEY (paciente_id) REFERENCES pacientes (id)
            );
            CREATE TABLE usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                usuario TEXT UNIQUE NOT NULL,
                senha_hash TEXT NOT NULL,
                perfil TEXT NOT NULL,
                registro_profissional TEXT
            );
            INSERT INTO pacientes (id, nome) VALUES (41, 'Paciente legado');
            INSERT INTO odontograma
                (id, paciente_id, dente, face, condicao, codigo_sigtap)
            VALUES (71, 41, '11', 'geral', 'restaurado', '01.01.02.002-3');
            INSERT INTO usuarios (id, nome, usuario, senha_hash, perfil)
            VALUES (1, 'Admin legado', 'admin', 'hash-preservado', 'ADMIN');
            """
        )

    database.criar_tabelas()

    with database.conectar() as conn:
        patient_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(pacientes)")
        }
        user_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(usuarios)")
        }
        patient = conn.execute(
            "SELECT id, nome, cnes_unidade FROM pacientes WHERE id=41"
        ).fetchone()
        dental_record = conn.execute(
            "SELECT id, paciente_id FROM odontograma WHERE id=71"
        ).fetchone()
        admin = conn.execute(
            "SELECT id, senha_hash FROM usuarios WHERE id=1"
        ).fetchone()

    assert "cnes_unidade" in patient_columns
    assert "cnes_unidade" in user_columns
    assert tuple(patient) == (41, "Paciente legado", None)
    assert tuple(dental_record) == (71, 41)
    assert tuple(admin) == (1, "hash-preservado")


def test_existing_user_can_login_and_access_authorized_modules(auth_client):
    response = _login(auth_client, "admin", "secure-admin-password")
    assert response.status_code == 302
    assert response.location == "/"

    for path in (
        "/",
        "/usuarios",
        "/pacientes",
        "/agenda",
        "/prontuario/",
        "/odonto/",
        "/farmacia-sus/",
        "/gestao-publica/",
        "/integracoes/",
    ):
        assert auth_client.get(path, follow_redirects=True).status_code == 200, path
    agenda = auth_client.get("/agenda")
    assert "Calendário e Agendamentos".encode() in agenda.data


def test_wrong_password_does_not_create_an_authenticated_session(auth_client):
    response = _login(auth_client, "admin", "incorrect-password")
    assert response.status_code == 200
    assert "Usuário ou senha incorretos.".encode() in response.data
    with auth_client.session_transaction() as session:
        assert "user_id" not in session


def test_logout_requires_csrf_and_clears_the_session(auth_client):
    _login(auth_client, "admin", "secure-admin-password")
    page = auth_client.get("/")
    match = re.search(
        r'name="logout_csrf" value="([^"]+)"',
        page.get_data(as_text=True),
    )
    assert match
    assert auth_client.get("/logout").status_code == 405
    assert auth_client.post("/logout").status_code == 400
    response = auth_client.post("/logout", data={"logout_csrf": match.group(1)})
    assert response.status_code == 302
    with auth_client.session_transaction() as session:
        assert "user_id" not in session


def test_user_creation_requires_admin_csrf_and_strong_hashed_password(auth_client):
    page = auth_client.get("/usuarios")
    assert page.status_code == 302

    with auth_client.session_transaction() as session:
        session["user_id"] = 1
        session["user_nome"] = "Atendente"
        session["user_perfil"] = "RECEPCAO"
    assert auth_client.get("/usuarios").status_code == 403

    with auth_client.session_transaction() as session:
        session["user_perfil"] = "ADMIN"
    page = auth_client.get("/usuarios")
    assert page.status_code == 200
    token = _csrf_token(page)
    user_data = {
        "csrf_token": token,
        "nome": "Profissional de teste",
        "usuario": "profissional",
        "senha": "strong-professional-password",
        "perfil": "DENTISTA",
        "registro_profissional": "CRO-123",
        "cnes_unidade": "1234567",
    }
    assert auth_client.post("/usuarios", data={**user_data, "csrf_token": "invalid"}).status_code == 400
    response = auth_client.post("/usuarios", data=user_data)
    assert response.status_code == 302
    user = database.buscar_usuario_por_login("profissional")
    assert user["perfil"] == "DENTISTA"
    assert user["senha_hash"] != user_data["senha"]
    assert check_password_hash(user["senha_hash"], user_data["senha"])
    assert database.autenticar_usuario("profissional", user_data["senha"])
    assert auth_client.post("/usuarios", data=user_data).status_code == 200

    api_data = {
        "nome": "Usuário via API",
        "usuario": "api-user",
        "senha": "strong-api-user-password",
        "perfil": "FARMACIA",
        "cnes_unidade": "1234567",
    }
    missing_unit_response = auth_client.post(
        "/api/auth/register",
        json={key: value for key, value in api_data.items() if key != "cnes_unidade"},
        headers={"X-CSRF-Token": token},
    )
    assert missing_unit_response.status_code == 400
    api_response = auth_client.post(
        "/api/auth/register",
        json=api_data,
        headers={"X-CSRF-Token": token},
    )
    assert api_response.status_code == 201
    assert database.autenticar_usuario("api-user", api_data["senha"])


def test_profiles_are_enforced_on_clinical_and_pharmacy_routes(auth_client):
    assert auth_client.post("/api/auth/register", json={}).status_code == 401
    with auth_client.session_transaction() as session:
        session["user_id"] = 2
        session["user_nome"] = "Atendente"
        session["user_perfil"] = "RECEPCAO"
    assert auth_client.get("/pacientes").status_code == 403
    assert auth_client.get("/agenda").status_code == 403
    assert auth_client.get("/farmacia-sus/").status_code == 403
    assert auth_client.get("/gestao-publica/").status_code == 403

    with auth_client.session_transaction() as session:
        session["user_perfil"] = "FARMACIA"
    assert auth_client.get("/farmacia-sus/").status_code == 200
    assert auth_client.get("/pacientes").status_code == 403
    assert auth_client.get("/agenda").status_code == 403


def test_initial_admin_environment_secret_never_changes_existing_accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_NAME", str(tmp_path / "existing.db"))
    database.criar_tabelas()
    conn = database.conectar()
    try:
        conn.execute(
            """
            INSERT INTO usuarios (nome, usuario, senha_hash, perfil, registro_profissional)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "Administrador legado",
                "admin",
                generate_password_hash("admin123"),
                "ADMIN",
                "MASTER",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "do-not-rotate-existing-account")
    database.criar_usuario_admin_inicial()
    assert database.autenticar_usuario("admin", "admin123") is None
    assert database.autenticar_usuario("admin", "do-not-rotate-existing-account") is None
    assert len(database.listar_usuarios()) == 1


def test_admin_recovery_backs_up_and_changes_only_the_legacy_admin(auth_client, tmp_path):
    conn = database.conectar()
    try:
        conn.execute(
            "UPDATE usuarios SET senha_hash = ? WHERE usuario = ?",
            (generate_password_hash("admin123"), "admin"),
        )
        conn.execute(
            """
            INSERT INTO usuarios (nome, usuario, senha_hash, perfil, registro_profissional)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "Outro usuário",
                "outro",
                generate_password_hash("unrelated-user-password"),
                "DENTISTA",
                "",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    runner = app_module.app.test_cli_runner()
    result = runner.invoke(
        args=["recover-legacy-admin"],
        input="recovered-admin-password\nrecovered-admin-password\n",
    )
    assert result.exit_code == 0, result.output
    assert "recovered-admin-password" not in result.output
    assert database.autenticar_usuario("admin", "recovered-admin-password")
    assert database.autenticar_usuario("admin", "admin123") is None
    assert database.autenticar_usuario("outro", "unrelated-user-password")

    backup_paths = list((tmp_path / "backups").glob("*.db"))
    assert len(backup_paths) == 1
    with sqlite3.connect(backup_paths[0]) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        saved_hash = backup.execute(
            "SELECT senha_hash FROM usuarios WHERE usuario = ?", ("admin",)
        ).fetchone()[0]
        assert check_password_hash(saved_hash, "admin123")


def test_admin_password_reset_backs_up_and_changes_only_admin(auth_client, tmp_path):
    conn = database.conectar()
    try:
        conn.execute(
            """
            INSERT INTO usuarios (nome, usuario, senha_hash, perfil, registro_profissional)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "Outro usuário",
                "outro",
                generate_password_hash("unrelated-user-password"),
                "DENTISTA",
                "",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    runner = app_module.app.test_cli_runner()
    result = runner.invoke(
        args=["reset-admin-password"],
        input="known-admin-password-2026\nknown-admin-password-2026\n",
    )
    assert result.exit_code == 0, result.output
    assert "known-admin-password-2026" not in result.output
    assert database.autenticar_usuario("admin", "known-admin-password-2026")
    assert database.autenticar_usuario("admin", "admin123") is None
    assert database.autenticar_usuario("outro", "unrelated-user-password")

    backup_paths = list((tmp_path / "backups").glob("*.db"))
    assert len(backup_paths) == 1
    with sqlite3.connect(backup_paths[0]) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        saved_hash = backup.execute(
            "SELECT senha_hash FROM usuarios WHERE usuario = ?", ("admin",)
        ).fetchone()[0]
        assert not check_password_hash(saved_hash, "known-admin-password-2026")
