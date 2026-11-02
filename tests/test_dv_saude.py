import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))
_isolated_runtime = tempfile.TemporaryDirectory(prefix="dv-saude-tests-")
_original_cwd = os.getcwd()
_original_database_path = os.environ.get("DV_DATABASE_PATH")
os.environ["DV_DATABASE_PATH"] = str(
    Path(_isolated_runtime.name) / "dv_odonto.db"
)
os.chdir(_isolated_runtime.name)
try:
    import app as app_module
    import commercial
    import dv_saude
finally:
    os.chdir(_original_cwd)
    if _original_database_path is None:
        os.environ.pop("DV_DATABASE_PATH", None)
    else:
        os.environ["DV_DATABASE_PATH"] = _original_database_path


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "dv_saude_test.db"
    monkeypatch.setattr(
        app_module.database, "DB_NAME", str(tmp_path / "application_test.db")
    )
    app_module.database.criar_tabelas()
    commercial.ensure_schema()
    conn = app_module.database.conectar()
    try:
        conn.execute(
            "INSERT INTO dv_unidades (cnes, nome) VALUES (?, ?)",
            ("1234567", "Unidade de Teste"),
        )
        conn.commit()
    finally:
        conn.close()
    app_module.database.cadastrar_usuario("Teste", "admin", "ADMIN")
    app_module.database.cadastrar_usuario(
        "Recepção", "recepcao", "RECEPCAO", "", "1234567"
    )
    admin = app_module.database.buscar_usuario_por_login("admin")
    app_module.database.definir_senha_usuario(admin["id"], "valid-test-password")

    def connect():
        connection = sqlite3.connect(db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    monkeypatch.setattr(dv_saude, "_connect", connect)
    dv_saude._ensure_schema()
    connection = connect()
    connection.executescript(
        """
        CREATE TABLE pacientes (id INTEGER PRIMARY KEY, nome TEXT NOT NULL);
        CREATE TABLE agenda (id INTEGER PRIMARY KEY);
        CREATE TABLE prontuario_soap (id INTEGER PRIMARY KEY);
        CREATE TABLE usuarios (
            id INTEGER PRIMARY KEY,
            nome TEXT NOT NULL,
            usuario TEXT NOT NULL,
            perfil TEXT NOT NULL,
            registro_profissional TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO dv_unidades (cnes, nome) VALUES (?, ?)",
        ("1234567", "Unidade de Teste"),
    )
    connection.execute(
        "INSERT INTO usuarios (id, nome, usuario, perfil, registro_profissional) VALUES (?, ?, ?, ?, ?)",
        (admin["id"], admin["nome"], "admin", "ADMIN", ""),
    )
    connection.commit()
    connection.close()
    dv_saude._ensure_schema()
    app_module.app.config.update(TESTING=True)
    test_client = app_module.app.test_client()
    admin_fresh = app_module.database.buscar_usuario_por_login("admin")
    with test_client.session_transaction() as session:
        session["user_id"] = admin_fresh["id"]
        session["user_nome"] = admin_fresh["nome"]
        session["user_perfil"] = admin_fresh["perfil"]
        session["auth_version"] = admin_fresh["session_version"]
        session["cnes_unidade"] = admin_fresh.get("cnes_unidade") or ""
    return test_client


def _form_value(html, name):
    match = re.search(rf'name="{name}" value="([^"]+)"', html)
    assert match
    return match.group(1)


def test_portal_lists_all_divisions_and_odonto_route_remains_available(client):
    response = client.get("/")
    assert response.status_code == 200
    for module in ("DV Prontuário", "DV Odonto", "DV Farmácia SUS", "DV Gestão Pública", "DV Integrações"):
        assert module.encode() in response.data

    response = client.get("/odonto/")
    assert response.status_code == 200
    assert b"Odontograma" in response.data
    assert client.get("/odontograma").status_code == 200


def test_each_division_and_pharmacy_subroute_has_a_real_page(client):
    for path in (
        "/prontuario/",
        "/farmacia-sus/",
        "/farmacia-sus/medicamentos/",
        "/farmacia-sus/estoque/",
        "/farmacia-sus/movimentacoes/",
        "/farmacia-sus/dispensacoes/",
        "/farmacia-sus/relatorios/",
        "/gestao-publica/",
        "/integracoes/",
        "/configuracoes/",
        "/busca/?q=teste",
    ):
        assert client.get(path, follow_redirects=True).status_code == 200, path

    assert client.get("/farmacia-sus/relatorios/?inicio=not-a-date").status_code == 400
    assert client.get("/busca/?q=" + "x" * 90).status_code == 400


def test_clinical_dashboard_recipe_and_report_are_scoped_to_active_unit(client):
    conn = app_module.database.conectar()
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO dv_unidades (cnes, nome) VALUES (?, ?)",
            [("1234567", "Unidade Um"), ("7654321", "Unidade Dois")],
        )
        conn.executemany(
            "INSERT INTO pacientes (nome, cnes_unidade) VALUES (?, ?)",
            [("Paciente Unidade Um", "1234567"), ("Paciente Unidade Dois", "7654321")],
        )
        patient_ids = {
            row["nome"]: row["id"]
            for row in conn.execute(
                "SELECT id, nome FROM pacientes ORDER BY id"
            ).fetchall()
        }
        conn.executemany(
            """
            INSERT INTO agenda (paciente_id, data_hora, especialidade, procedimento)
            VALUES (?, ?, ?, ?)
            """,
            [
                (patient_ids["Paciente Unidade Um"], "2026-10-18 09:00", "Odonto", "Consulta"),
                (patient_ids["Paciente Unidade Dois"], "2026-10-18 10:00", "Odonto", "Consulta"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO odontograma (paciente_id, dente, face, condicao, codigo_sigtap)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (patient_ids["Paciente Unidade Um"], "11", "geral", "restaurado", "01.01.02.002-3"),
                (patient_ids["Paciente Unidade Dois"], "12", "geral", "restaurado", "03.07.01.002-3"),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    assert app_module.database.cadastrar_usuario(
        "Dentista Unidade Um", "dentista-unidade-um", "DENTISTA", "", "1234567"
    )
    dentist = app_module.database.buscar_usuario_por_login("dentista-unidade-um")
    with client.session_transaction() as session:
        session["user_perfil"] = dentist["perfil"]
        session["cnes_unidade"] = dentist["cnes_unidade"]
        session["user_id"] = dentist["id"]
        session["auth_version"] = dentist["session_version"]

    dashboard = client.get("/")
    assert b"Pacientes cadastrados</span><strong>1</strong>" in dashboard.data
    assert b"Consultas registradas</span><strong>1</strong>" in dashboard.data

    recipe = client.get("/receita")
    assert b"Paciente Unidade Um" in recipe.data
    assert b"Paciente Unidade Dois" not in recipe.data

    report = client.get("/relatorio")
    assert b"01.01.02.002-3" in report.data
    assert b"03.07.01.002-3" not in report.data


def test_new_modules_require_authentication_and_admin_roles(client):
    response = client.get("/farmacia-sus/")
    assert response.status_code == 200

    with client.session_transaction() as session:
        session.clear()
    assert client.get("/farmacia-sus/").status_code == 302

    with client.session_transaction() as session:
        session["user_id"] = 2
        session["user_nome"] = "Recepção"
        recepcao = app_module.database.buscar_usuario_por_login("recepcao")
        session["auth_version"] = recepcao["session_version"]
        session["user_perfil"] = "RECEPCAO"
    assert client.get("/farmacia-sus/").status_code == 403
    assert client.get("/gestao-publica/").status_code == 403


def test_anonymous_portal_hides_database_counters_and_login_requires_csrf(client):
    with client.session_transaction() as session:
        session.clear()
    response = client.get("/")
    assert response.status_code == 200
    assert b"Pacientes cadastrados" not in response.data

    login_page = client.get("/login")
    assert login_page.status_code == 200
    assert b"Senha" in login_page.data
    assert b"PIN" in login_page.data
    assert client.post(
        "/login", data={"usuario": "admin", "senha": "valid-test-password", "modo": "senha"}
    ).status_code == 400
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', login_page.get_data(as_text=True)).group(1)
    assert client.post(
        "/login", data={"usuario": "admin", "senha": "valid-test-password", "modo": "senha", "csrf_token": csrf}
    ).status_code in (200, 302)


def test_password_login_rejects_external_redirects(client):
    with client.session_transaction() as session:
        session.clear()
    page = client.get("/login?next=https://outside.example")
    assert page.status_code == 200
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)
    response = client.post(
        "/login?next=https://outside.example",
        data={
            "modo": "senha",
            "usuario": "admin",
            "senha": "valid-test-password",
            "next": "https://outside.example",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/"


def test_first_admin_requires_an_explicit_passkey_provisioning_command(
    tmp_path, monkeypatch
):
    database = app_module.database
    monkeypatch.setattr(database, "DB_NAME", str(tmp_path / "bootstrap.db"))
    database.criar_tabelas()
    assert database.buscar_usuario_por_login("admin") is None
    assert database.criar_primeiro_admin("Operador", "admin")
    admin = database.buscar_usuario_por_login("admin")
    assert admin["perfil"] == "ADMIN"
    assert not admin["senha_hash"]
    assert database.criar_convite_passkey(admin["id"])
    assert database.autenticar_usuario("admin", "any-password") is None


def test_global_search_paginates_results(client):
    conn = dv_saude._connect()
    try:
        conn.executemany(
            "INSERT INTO pacientes(nome) VALUES (?)",
            [(f"Paciente paginação {index:02d}",) for index in range(25)],
        )
        conn.commit()
    finally:
        conn.close()

    first_page = client.get("/busca/?q=paginação&page=1")
    second_page = client.get("/busca/?q=paginação&page=2")
    assert first_page.status_code == 200
    assert second_page.status_code == 200
    assert first_page.get_data(as_text=True).count("Paciente: Paciente") == 20
    assert second_page.get_data(as_text=True).count("Paciente: Paciente") == 5
    assert "Página 2 de 2" in second_page.get_data(as_text=True)


def test_stock_entries_are_transactional_and_cannot_dispense_above_balance(client):
    page = client.get("/farmacia-sus/medicamentos/")
    token = _form_value(page.get_data(as_text=True), "csrf_token")
    response = client.post(
        "/farmacia-sus/medicamentos/",
        data={
            "csrf_token": token,
            "nome": "Medicamento teste",
            "principio_ativo": "Ativo",
            "apresentacao": "Comprimido",
            "unidade_medida": "comprimido",
            "estoque_minimo": "2",
        },
    )
    assert response.status_code == 302, response.get_data(as_text=True)

    page = client.get("/farmacia-sus/estoque/")
    html = page.get_data(as_text=True)
    token = _form_value(html, "csrf_token")
    operation_key = _form_value(html, "chave_operacao")
    response = client.post(
        "/farmacia-sus/estoque/",
        data={
            "csrf_token": token,
            "chave_operacao": operation_key,
            "medicamento_id": "1",
            "cnes_unidade": "1234567",
            "lote": "L-001",
            "validade": "2030-12-31",
            "quantidade": "3",
            "justificativa": "Entrada inicial",
        },
    )
    assert response.status_code == 302, response.get_data(as_text=True)

    page = client.get("/farmacia-sus/dispensacoes/")
    html = page.get_data(as_text=True)
    response = client.post(
        "/farmacia-sus/dispensacoes/",
        data={
            "csrf_token": _form_value(html, "csrf_token"),
            "chave_operacao": _form_value(html, "chave_operacao"),
            "medicamento_id": "1",
            "cnes_unidade": "1234567",
            "lote": "L-001",
            "validade": "2030-12-31",
            "quantidade": "4",
            "justificativa": "Dispensação de teste",
        },
    )
    assert response.status_code == 200
    assert "Saldo insuficiente".encode() in response.data

    conn = dv_saude._connect()
    try:
        assert conn.execute("SELECT saldo FROM dv_farmacia_lotes").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM dv_farmacia_movimentacoes").fetchone()[0] == 1
    finally:
        conn.close()
