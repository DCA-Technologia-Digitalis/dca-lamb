"""
tests/test_authentication.py
Testes de autenticacao por senha e PIN.
Nao dependem de fido2 nem de passkey.
"""
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import pytest
import pyotp

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

_isolated_dir = tempfile.TemporaryDirectory(prefix="dv-test-")
_original_cwd = os.getcwd()
_original_db = os.environ.get("DV_DATABASE_PATH")
os.environ["DV_DATABASE_PATH"] = str(Path(_isolated_dir.name) / "dv_odonto.db")
os.chdir(_isolated_dir.name)
try:
    import app as app_module
    import commercial
    import database
    import dv_saude
finally:
    os.chdir(_original_cwd)
    if _original_db is None:
        os.environ.pop("DV_DATABASE_PATH", None)
    else:
        os.environ["DV_DATABASE_PATH"] = _original_db


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_NAME", str(db_path))
    database.criar_tabelas()
    dv_saude._ensure_schema()
    commercial.ensure_schema()
    conn = database.conectar()
    try:
        conn.execute("INSERT INTO dv_unidades (cnes, nome) VALUES (?, ?)", ("1234567", "Unidade Teste"))
        conn.commit()
    finally:
        conn.close()
    assert database.cadastrar_usuario("Admin Teste", "admin", "ADMIN")
    admin = database.buscar_usuario_por_login("admin")
    assert database.definir_senha_usuario(admin["id"], "senha-valida-123")
    app_module.app.config.update(TESTING=True, SECRET_KEY="test-key-only")
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s.clear()
    return c


def _login_as(client, user):
    with client.session_transaction() as s:
        s.clear()
        s["user_id"] = user["id"]
        s["user_nome"] = user["nome"]
        s["user_perfil"] = user["perfil"]
        s["cnes_unidade"] = user.get("cnes_unidade") or ""
        s["auth_version"] = user["session_version"]


def _complete_super_admin_setup(client):
    page = client.get("/dca/mfa/setup")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    secret = re.search(r"<code>([A-Z2-7]+)</code>", html).group(1)
    token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
    response = client.post(
        "/dca/mfa/setup", data={"csrf_token": token, "code": pyotp.TOTP(secret).now()}
    )
    assert response.status_code == 302
    assert "/dca/mfa/recovery-codes" in response.headers["Location"]
    recovery_page = client.get(response.headers["Location"])
    codes = re.findall(r"\b[A-F0-9]{5}-[A-F0-9]{5}\b", recovery_page.get_data(as_text=True))
    assert len(codes) == 10
    return codes


def _post_login(client, data, url="/login"):
    page = client.get(url)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)
    return client.post(url, data={**data, "csrf_token": token})


# ── Login por senha ───────────────────────────────────────────────────────────

def test_login_page_carrega(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert b"Senha" in r.data


def test_login_senha_correta_redireciona(client):
    r = _post_login(client, {"modo": "senha", "usuario": "admin", "senha": "senha-valida-123"})
    assert r.status_code == 302
    assert r.headers["Location"] == "/"


def test_login_senha_errada_retorna_erro(client):
    r = _post_login(client, {"modo": "senha", "usuario": "admin", "senha": "errada"})
    assert r.status_code == 200
    assert "incorretos" in r.get_data(as_text=True)


def test_login_usuario_inexistente(client):
    r = _post_login(client, {"modo": "senha", "usuario": "naoexiste", "senha": "qualquer"})
    assert r.status_code == 200
    assert "incorretos" in r.get_data(as_text=True)


def test_usuario_logado_redireciona_para_index(client):
    user = database.buscar_usuario_por_login("admin")
    _login_as(client, user)
    r = client.get("/login")
    assert r.status_code == 302
    assert r.headers["Location"] == "/"


# ── PIN ───────────────────────────────────────────────────────────────────────

def test_definir_e_verificar_pin(client):
    user = database.buscar_usuario_por_login("admin")
    assert database.definir_pin_usuario(user["id"], "1234")
    assert database.verificar_pin_usuario(user["id"], "1234")
    assert not database.verificar_pin_usuario(user["id"], "0000")


def test_pin_invalido_rejeitado(client):
    user = database.buscar_usuario_por_login("admin")
    with pytest.raises(ValueError):
        database.definir_pin_usuario(user["id"], "12")
    with pytest.raises(ValueError):
        database.definir_pin_usuario(user["id"], "abcd")
    with pytest.raises(ValueError):
        database.definir_pin_usuario(user["id"], "12345")


def test_vincular_unidade_a_organizacao_preserva_e_atualiza_dados(client):
    conn = database.conectar()
    try:
        conn.execute("INSERT INTO pacientes (nome, cnes_unidade) VALUES (?, ?)", ("Paciente Fictício", "1234567"))
        patient_id = conn.execute("SELECT id FROM pacientes WHERE nome=?", ("Paciente Fictício",)).fetchone()["id"]
        conn.execute(
            "INSERT INTO agenda (paciente_id, data_hora, especialidade, procedimento) VALUES (?, ?, ?, ?)",
            (patient_id, "2026-10-01 09:00", "Odontologia", "Consulta"),
        )
        conn.commit()
    finally:
        conn.close()
    admin = database.buscar_usuario_por_login("admin")
    organization_id = database.criar_organizacao_e_vincular_unidade(
        "Clínica de Teste", "1234567", admin["id"], "test-request-id"
    )
    assert database.obter_organizacao_unidade("1234567") == organization_id
    conn = database.conectar()
    try:
        patient = conn.execute("SELECT organization_id, cnes_unidade FROM pacientes WHERE id=?", (patient_id,)).fetchone()
        appointment = conn.execute("SELECT organization_id FROM agenda WHERE paciente_id=?", (patient_id,)).fetchone()
        audit = conn.execute("SELECT action, request_id FROM audit_events WHERE resource_type='organization'").fetchone()
    finally:
        conn.close()
    assert patient["organization_id"] == organization_id
    assert patient["cnes_unidade"] == "1234567"
    assert appointment["organization_id"] == organization_id
    assert audit["action"] == "admin_organization_create"
    assert audit["request_id"] == "test-request-id"
    conn = database.conectar()
    try:
        conn.execute("INSERT INTO dv_unidades(cnes, nome) VALUES (?, ?)", ("7654321", "Outra Unidade Fictícia"))
        conn.commit()
    finally:
        conn.close()
    database.criar_organizacao_e_vincular_unidade(
        "", "7654321", admin["id"], "test-assign-id", organization_id
    )
    assert database.obter_organizacao_unidade("7654321") == organization_id
    assert next(o for o in database.listar_organizacoes() if o["id"] == organization_id)["unidades"] == 2


def test_cadastro_dca_cria_organizacao_especialidades_e_assinatura_pendente(client):
    user_id = commercial.register_customer(
        {
            "organization_name": "Clínica Fictícia DCA",
            "contact_name": "Responsável de Teste",
            "email": "responsavel@example.invalid",
            "account_type": "ESTABLISHMENT",
            "primary_specialty": "Odontologia",
            "specialties": "Clínica Geral\nFisioterapia",
            "professional_count": "3",
            "unit_count": "2",
            "password": "senha-de-teste-com-12",
            "confirm_data": "yes",
        },
        "request-test-commercial",
    )
    owner = database.buscar_usuario_por_id(user_id)
    overview = commercial.get_customer_dashboard(owner["organization_id"])
    assert owner["perfil"] == "ORG_ADMIN"
    assert overview["status"] == "PENDING"
    assert overview["price_cents"] is None
    assert overview["billing_interval"] is None
    assert len(overview["specialties"]) == 3
    assert len(overview["modules"]) == 7
    assert overview["payments"] == []
    assert commercial.module_enabled(owner["organization_id"], "odonto") is False


def test_cadastro_incompleto_nao_cria_organizacao_ou_assinatura(client):
    conn = database.conectar()
    try:
        organization_count_before = conn.execute("SELECT COUNT(*) AS n FROM organizacoes").fetchone()["n"]
        subscription_count_before = conn.execute("SELECT COUNT(*) AS n FROM dca_subscriptions").fetchone()["n"]
    finally:
        conn.close()
    with pytest.raises(ValueError):
        commercial.register_customer({
            "organization_name": "Cadastro Incompleto Sintetico",
            "email": "incomplete@example.invalid",
        })
    conn = database.conectar()
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM organizacoes").fetchone()["n"] == organization_count_before
        assert conn.execute("SELECT COUNT(*) AS n FROM dca_subscriptions").fetchone()["n"] == subscription_count_before
    finally:
        conn.close()


def test_esquema_comercial_pode_ser_criado_vazio_para_importacao(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_NAME", str(tmp_path / "empty-target.sqlite3"))
    monkeypatch.setattr(database, "_USE_POSTGRES", False)
    database.criar_tabelas()
    dv_saude._ensure_schema()
    commercial.ensure_schema(seed_catalog=False)
    conn = database.conectar()
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM dca_modules").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM dca_plans").fetchone()["n"] == 0
    finally:
        conn.close()
    commercial.ensure_schema()
    conn = database.conectar()
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM dca_modules").fetchone()["n"] == 7
        assert conn.execute("SELECT COUNT(*) AS n FROM dca_plans").fetchone()["n"] == 1
    finally:
        conn.close()


def test_analise_comercial_nao_ativa_assinatura_nem_cria_pagamento(client):
    user_id = commercial.register_customer(
        {
            "organization_name": "Consultório Fictício",
            "contact_name": "Proprietária de Teste",
            "email": "proprietaria@example.invalid",
            "account_type": "INDIVIDUAL",
            "primary_specialty": "Odontologia",
            "specialties": "",
            "professional_count": "1",
            "unit_count": "0",
            "password": "senha-de-teste-com-12",
            "confirm_data": "yes",
        }
    )
    user = database.buscar_usuario_por_id(user_id)
    conn = database.conectar()
    try:
        subscription = conn.execute(
            "SELECT id FROM dca_subscriptions WHERE organization_id=?",
            (user["organization_id"],),
        ).fetchone()
    finally:
        conn.close()
    assert commercial.subscription_status(user["organization_id"]) == "PENDING"
    conn = database.conectar()
    try:
        assert conn.execute("SELECT COUNT(*) FROM dca_payments").fetchone()[0] == 0
    finally:
        conn.close()


def test_cliente_cadastra_unidade_e_usuario_apenas_na_propria_organizacao(client):
    owner_id = commercial.register_customer(
        {
            "organization_name": "Organização Fictícia Um",
            "contact_name": "Responsável Um",
            "email": "owner-one@example.invalid",
            "account_type": "ESTABLISHMENT",
            "primary_specialty": "Odontologia",
            "specialties": "",
            "professional_count": "1",
            "unit_count": "1",
            "password": "senha-de-teste-com-12",
            "confirm_data": "yes",
        }
    )
    owner = database.buscar_usuario_por_id(owner_id)
    conn = database.conectar()
    try:
        other_org = conn.execute(
            "INSERT INTO organizacoes(nome, slug) VALUES (?, ?)",
            ("Outra Organização Fictícia", "other-test-org"),
        ).lastrowid
        conn.execute(
            "INSERT INTO dv_unidades(cnes, nome, organization_id) VALUES (?, ?, ?)",
            ("7654321", "Unidade Externa Fictícia", other_org),
        )
        conn.commit()
    finally:
        conn.close()
    with client.session_transaction() as session:
        session.clear()
        session.update(
            user_id=owner["id"], user_nome=owner["nome"], user_perfil=owner["perfil"],
            cnes_unidade="", organization_id=owner["organization_id"],
            auth_version=owner["session_version"],
        )

    unit_page = client.get("/cliente/unidades")
    token = re.search(r'name="csrf_token" value="([^"]+)"', unit_page.get_data(as_text=True)).group(1)
    response = client.post(
        "/cliente/unidades",
        data={"csrf_token": token, "name": "Unidade Própria Fictícia", "cnes": "3333333"},
    )
    assert response.status_code == 302

    users_page = client.get("/cliente/usuarios")
    token = re.search(r'name="csrf_token" value="([^"]+)"', users_page.get_data(as_text=True)).group(1)
    denied = client.post(
        "/cliente/usuarios",
        data={"csrf_token": token, "name": "Usuário Fictício", "email": "outside@example.invalid",
              "profile": "DENTISTA", "cnes": "7654321"},
    )
    assert denied.status_code == 200
    assert "vinculada à sua organização" in denied.get_data(as_text=True)

    users_page = client.get("/cliente/usuarios")
    token = re.search(r'name="csrf_token" value="([^"]+)"', users_page.get_data(as_text=True)).group(1)
    created = client.post(
        "/cliente/usuarios",
        data={"csrf_token": token, "name": "Profissional Fictício", "email": "staff@example.invalid",
              "profile": "DENTISTA", "cnes": "3333333"},
    )
    assert created.status_code == 200
    assert "Convite de uso único" in created.get_data(as_text=True)
    staff = database.buscar_usuario_por_login("staff@example.invalid")
    assert staff["organization_id"] == owner["organization_id"]
    users_page = client.get("/cliente/usuarios")
    token = re.search(r'name="csrf_token" value="([^"]+)"', users_page.get_data(as_text=True)).group(1)
    changed = client.post(
        "/cliente/usuarios",
        data={"csrf_token": token, "action": "change_profile", "user_id": staff["id"], "profile": "FARMACIA"},
    )
    assert changed.status_code == 200
    assert database.buscar_usuario_por_id(staff["id"])["perfil"] == "FARMACIA"
    assert client.get("/odonto/").status_code == 302


def test_super_admin_dca_gerencia_metadados_sem_ativar_assinatura(client):
    owner_id = commercial.register_customer(
        {
            "organization_name": "Clínica Fictícia de Aprovação",
            "contact_name": "Responsável Comercial",
            "email": "owner-approval@example.invalid",
            "account_type": "ESTABLISHMENT",
            "primary_specialty": "Medicina",
            "specialties": "Odontologia",
            "professional_count": "2",
            "unit_count": "1",
            "password": "senha-de-teste-com-12",
            "confirm_data": "yes",
        }
    )
    owner = database.buscar_usuario_por_id(owner_id)
    conn = database.conectar()
    try:
        conn.execute("INSERT INTO pacientes(nome) VALUES (?)", ("Paciente Clínico Fictício",))
        conn.commit()
    finally:
        conn.close()
    admin = database.buscar_usuario_por_login("admin")
    assert client.get("/dca/admin").status_code == 302
    conn = database.conectar()
    try:
        conn.execute("UPDATE usuarios SET perfil='DCA_SUPER_ADMIN' WHERE id=?", (admin["id"],))
        conn.commit()
    finally:
        conn.close()
    admin = database.buscar_usuario_por_id(admin["id"])
    assert all(user["perfil"] != "DCA_SUPER_ADMIN" for user in database.listar_usuarios())
    primary_login = _post_login(
        client, {"modo": "senha", "usuario": "admin", "senha": "senha-valida-123"}
    )
    assert primary_login.status_code == 302
    assert "/dca/mfa/setup" in primary_login.headers["Location"]
    _complete_super_admin_setup(client)
    page = client.get("/dca/admin")
    assert page.status_code == 200
    assert b"Cl\xc3\xadnica Fict\xc3\xadcia de Aprova\xc3\xa7\xc3\xa3o" in page.data
    assert b"Paciente Cl\xc3\xadnico Fict\xc3\xadcio" not in page.data
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)
    conn = database.conectar()
    try:
        subscription = conn.execute(
            "SELECT id FROM dca_subscriptions WHERE organization_id=?",
            (owner["organization_id"],),
        ).fetchone()
    finally:
        conn.close()
    response = client.post(
        "/dca/admin",
        data={"csrf_token": token, "subscription_id": subscription["id"]},
    )
    assert response.status_code == 200
    assert "Nenhuma assinatura foi ativada" in response.get_data(as_text=True)
    assert commercial.subscription_status(owner["organization_id"]) == "UNDER_REVIEW"
    assert not commercial.mark_subscription_review(subscription["id"], admin["id"], "req-repeat")
    assert commercial.module_enabled(owner["organization_id"], "odonto") is False
    token = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True)).group(1)
    toggled = client.post(
        "/dca/admin",
        data={"csrf_token": token, "action": "toggle_module",
              "organization_id": owner["organization_id"], "module_code": "odonto", "enabled": "0"},
    )
    assert toggled.status_code == 200
    assert next(item for item in commercial.list_organization_modules(owner["organization_id"])
                if item["code"] == "odonto")["enabled"] == 0
    assert client.get("/pacientes").status_code == 403


def test_super_admin_mfa_obrigatorio_e_codigo_de_recuperacao_unico(client):
    admin = database.buscar_usuario_por_login("admin")
    conn = database.conectar()
    try:
        conn.execute("UPDATE usuarios SET perfil='DCA_SUPER_ADMIN' WHERE id=?", (admin["id"],))
        conn.commit()
    finally:
        conn.close()
    admin = database.buscar_usuario_por_id(admin["id"])

    _login_as(client, admin)
    denied = client.get("/dca/admin")
    assert denied.status_code == 302
    with client.session_transaction() as session:
        assert "user_id" not in session

    primary = _post_login(
        client, {"modo": "senha", "usuario": "admin", "senha": "senha-valida-123"}
    )
    assert "/dca/mfa/setup" in primary.headers["Location"]
    recovery_codes = _complete_super_admin_setup(client)
    assert client.get("/dca/admin").status_code == 200
    assert client.get("/api/pacientes").status_code == 403
    conn = database.conectar()
    try:
        encrypted_secret = conn.execute(
            "SELECT secret_encrypted FROM dca_super_admin_mfa WHERE user_id=?", (admin["id"],)
        ).fetchone()["secret_encrypted"]
        recovery_hashes = conn.execute(
            "SELECT code_hash FROM dca_super_admin_recovery_codes WHERE user_id=?", (admin["id"],)
        ).fetchall()
    finally:
        conn.close()
    with app_module.app.app_context():
        stored_secret = commercial._mfa_fernet().decrypt(encrypted_secret.encode()).decode()
    assert not any(code.replace("-", "") in str(row["code_hash"]) for code in recovery_codes for row in recovery_hashes)

    with client.session_transaction() as session:
        session.clear()
    second_primary = _post_login(
        client, {"modo": "senha", "usuario": "admin", "senha": "senha-valida-123"}
    )
    assert "/dca/mfa/verify" in second_primary.headers["Location"]
    verify_page = client.get("/dca/mfa/verify")
    token = re.search(r'name="csrf_token" value="([^"]+)"', verify_page.get_data(as_text=True)).group(1)
    next_step = (int(time.time()) // 30 + 1) * 30
    totp_code = pyotp.TOTP(stored_secret).at(next_step)
    accepted = client.post("/dca/mfa/verify", data={"csrf_token": token, "code": totp_code})
    assert accepted.status_code == 302
    assert client.get("/dca/admin").status_code == 200

    with client.session_transaction() as session:
        session.clear()
    third_primary = _post_login(
        client, {"modo": "senha", "usuario": "admin", "senha": "senha-valida-123"}
    )
    assert "/dca/mfa/verify" in third_primary.headers["Location"]
    verify_page = client.get("/dca/mfa/verify")
    token = re.search(r'name="csrf_token" value="([^"]+)"', verify_page.get_data(as_text=True)).group(1)
    first_code = recovery_codes[0]
    recovery_login = client.post("/dca/mfa/verify", data={"csrf_token": token, "code": first_code})
    assert recovery_login.status_code == 302
    with client.session_transaction() as session:
        session.clear()
    fourth_primary = _post_login(
        client, {"modo": "senha", "usuario": "admin", "senha": "senha-valida-123"}
    )
    assert "/dca/mfa/verify" in fourth_primary.headers["Location"]
    verify_page = client.get("/dca/mfa/verify")
    token = re.search(r'name="csrf_token" value="([^"]+)"', verify_page.get_data(as_text=True)).group(1)
    rejected = client.post("/dca/mfa/verify", data={"csrf_token": token, "code": first_code})
    assert rejected.status_code == 200
    assert client.get("/dca/admin").status_code == 302
    for _ in range(4):
        assert commercial.verify_mfa(admin["id"], "not-a-valid-code") is False
    conn = database.conectar()
    try:
        lock_state = conn.execute(
            "SELECT locked_until FROM dca_super_admin_mfa WHERE user_id=?", (admin["id"],)
        ).fetchone()["locked_until"]
    finally:
        conn.close()
    assert lock_state > int(time.time())


def test_isolamento_api_documentos_e_ciclo_de_assinatura(client):
    def create_org(name, email, cnes):
        owner_id = commercial.register_customer({
            "organization_name": name,
            "contact_name": "Responsável Fictício",
            "email": email,
            "account_type": "ESTABLISHMENT",
            "primary_specialty": "Odontologia",
            "specialties": "",
            "professional_count": "2",
            "unit_count": "1",
            "password": "senha-de-teste-com-12",
            "confirm_data": "yes",
        })
        owner = database.buscar_usuario_por_id(owner_id)
        conn = database.conectar()
        try:
            conn.execute(
                "INSERT INTO dv_unidades(cnes, nome, organization_id) VALUES (?, ?, ?)",
                (cnes, f"Unidade Fictícia {cnes}", owner["organization_id"]),
            )
            conn.commit()
        finally:
            conn.close()
        staff_id = commercial.register_customer_user(
            owner["organization_id"], f"Profissional {cnes}", f"dentista-{cnes}@example.invalid",
            "DENTISTA", cnes,
        )
        patient_id = database.adicionar_paciente(f"Paciente Sintético {cnes}", cnes_unidade=cnes)
        return owner, database.buscar_usuario_por_id(staff_id), patient_id

    owner_a, staff_a, patient_a = create_org(
        "Organização A Sintética", "owner-a@example.invalid", "3330001"
    )
    _owner_b, _staff_b, patient_b = create_org(
        "Organização B Sintética", "owner-b@example.invalid", "3330002"
    )
    assert database.adicionar_arquivo(
        patient_b, "Exame", "ficticio.pdf", "tenant-b-private.pdf", "application/pdf",
        cnes_unidade="3330002", sha256="b" * 64,
    )
    conn = database.conectar()
    try:
        conn.execute(
            "UPDATE dca_subscriptions SET status='ACTIVE' WHERE organization_id=?",
            (owner_a["organization_id"],),
        )
        conn.execute(
            "UPDATE dca_subscriptions SET status='ACTIVE' WHERE organization_id=?",
            (_owner_b["organization_id"],),
        )
        conn.commit()
    finally:
        conn.close()

    _login_as(client, staff_a)
    listed = client.get("/api/pacientes")
    assert listed.status_code == 200
    patient_ids = {item["id"] for item in listed.get_json()["pacientes"]}
    assert patient_a in patient_ids
    assert patient_b not in patient_ids
    assert database.listar_arquivos_paciente(patient_b, "3330001") == []
    assert client.get(f"/paciente/{patient_b}/editar").status_code == 302
    assert client.get(f"/paciente/{patient_b}/arquivo/tenant-b-private.pdf").status_code == 404
    assert client.get("/dca/admin").status_code == 403

    home = client.get("/").get_data(as_text=True)
    api_csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', home).group(1)
    created = client.post(
        "/api/pacientes", json={"nome": "Paciente criado por API fictício", "cnes_unidade": "3330002"},
        headers={"X-CSRF-Token": api_csrf},
    )
    assert created.status_code == 201
    created_patient = created.get_json()["paciente"]
    assert created_patient["cnes_unidade"] == "3330001"

    conn = database.conectar()
    try:
        subscription_id = conn.execute(
            "SELECT id FROM dca_subscriptions WHERE organization_id=?",
            (owner_a["organization_id"],),
        ).fetchone()["id"]
        conn.execute("UPDATE dca_subscriptions SET status='PENDING', next_renewal_at=NULL WHERE id=?", (subscription_id,))
        conn.commit()
    finally:
        conn.close()

    _login_as(client, owner_a)
    assert client.get("/cliente/").status_code == 200
    _login_as(client, staff_a)
    states = ("PENDING", "UNDER_REVIEW", "PAST_DUE", "SUSPENDED", "CANCELLED")
    for state in states:
        conn = database.conectar()
        try:
            conn.execute("UPDATE dca_subscriptions SET status=?, next_renewal_at=NULL WHERE id=?", (state, subscription_id))
            conn.commit()
        finally:
            conn.close()
        assert commercial.subscription_status(owner_a["organization_id"]) == state
        assert commercial.module_enabled(owner_a["organization_id"], "prontuario") is False
        assert client.get("/api/pacientes").status_code == 402
        assert client.get(f"/paciente/{patient_a}/editar").status_code == 302
        assert client.get("/documentos").status_code == 302
        _login_as(client, owner_a)
        assert client.get("/cliente/").status_code == 200
        _login_as(client, staff_a)

    conn = database.conectar()
    try:
        conn.execute(
            "UPDATE dca_subscriptions SET status='ACTIVE', next_renewal_at=? WHERE id=?",
            ("2000-01-01T00:00:00+00:00", subscription_id),
        )
        conn.commit()
    finally:
        conn.close()
    assert commercial.subscription_status(owner_a["organization_id"]) == "EXPIRED"
    assert client.get("/api/pacientes").status_code == 402


def test_login_pin_correto_redireciona(client):
    user = database.buscar_usuario_por_login("admin")
    database.definir_pin_usuario(user["id"], "9876")
    r = _post_login(client, {"modo": "pin", "usuario_pin": "admin", "pin": "9876"})
    assert r.status_code == 302


def test_login_pin_errado_retorna_erro(client):
    user = database.buscar_usuario_por_login("admin")
    database.definir_pin_usuario(user["id"], "9876")
    r = _post_login(client, {"modo": "pin", "usuario_pin": "admin", "pin": "0000"})
    assert r.status_code == 200
    assert "incorretos" in r.get_data(as_text=True)


def test_pin_bloqueio_apos_5_tentativas(client):
    user = database.buscar_usuario_por_login("admin")
    database.definir_pin_usuario(user["id"], "5555")
    for _ in range(5):
        database.verificar_pin_usuario(user["id"], "0000")
    assert database.pin_esta_bloqueado(user["id"])
    assert not database.verificar_pin_usuario(user["id"], "5555")


# ── Logout ────────────────────────────────────────────────────────────────────

def test_logout_limpa_sessao(client):
    user = database.buscar_usuario_por_login("admin")
    _login_as(client, user)
    page = client.get("/")
    m = re.search(r'name="logout_csrf" value="([^"]+)"', page.get_data(as_text=True))
    assert m
    r = client.post("/logout", data={"logout_csrf": m.group(1)})
    assert r.status_code == 302
    with client.session_transaction() as s:
        assert "user_id" not in s


def test_logout_sem_csrf_rejeitado(client):
    user = database.buscar_usuario_por_login("admin")
    _login_as(client, user)
    r = client.post("/logout", data={"logout_csrf": "invalido"})
    assert r.status_code == 400


def test_logout_get_nao_permitido(client):
    assert client.get("/logout").status_code == 405


def test_csrf_xss_sql_injection_e_cookie_seguro(client):
    admin = database.buscar_usuario_por_login("admin")
    _login_as(client, admin)
    patient_id = database.adicionar_paciente('<script>alert("x")</script>', cnes_unidade="1234567")
    listing = client.get("/pacientes").get_data(as_text=True)
    assert "&lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt;" in listing
    assert '<script>alert("x")</script>' not in listing

    assert client.post(
        "/api/pacientes", json={"nome": "Paciente CSRF fictício"}
    ).status_code == 400
    with client.session_transaction() as session:
        session.clear()
    rejected = _post_login(
        client, {"modo": "senha", "usuario": "' OR 1=1 --", "senha": "x"}
    )
    assert rejected.status_code == 200
    assert database.get_paciente(patient_id, "1234567") is not None

    old_secure = app_module.app.config["SESSION_COOKIE_SECURE"]
    app_module.app.config["SESSION_COOKIE_SECURE"] = True
    try:
        response = app_module.app.test_client().get("/login")
        cookie = response.headers.get("Set-Cookie", "")
        assert "HttpOnly" in cookie
        assert "Secure" in cookie
        assert "SameSite=Lax" in cookie
    finally:
        app_module.app.config["SESSION_COOKIE_SECURE"] = old_secure


# ── RBAC ──────────────────────────────────────────────────────────────────────

def test_rota_protegida_sem_login_redireciona(client):
    r = client.get("/pacientes")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_recepcao_nao_acessa_modulo_clinico(client):
    assert database.cadastrar_usuario("Recep", "recep", "RECEPCAO", "", "1234567")
    recep = database.buscar_usuario_por_login("recep")
    _login_as(client, recep)
    assert client.get("/pacientes").status_code == 403
    assert client.get("/agenda").status_code == 403


def test_admin_acessa_todos_modulos(client):
    user = database.buscar_usuario_por_login("admin")
    _login_as(client, user)
    assert client.get("/pacientes").status_code == 200
    assert client.get("/agenda").status_code == 200
    assert client.get("/usuarios").status_code == 200


# ── Senha ─────────────────────────────────────────────────────────────────────

def test_definir_senha_incrementa_session_version(client):
    user = database.buscar_usuario_por_login("admin")
    v = user["session_version"]
    database.definir_senha_usuario(user["id"], "nova-senha-456")
    assert database.buscar_usuario_por_login("admin")["session_version"] == v + 1


def test_senha_curta_rejeitada(client):
    user = database.buscar_usuario_por_login("admin")
    with pytest.raises(ValueError):
        database.definir_senha_usuario(user["id"], "123")


def test_autenticar_sem_senha_retorna_none(client):
    assert database.cadastrar_usuario("Sem Senha", "semsenhausr", "ADMIN")
    assert database.autenticar_usuario("semsenhausr", "qualquer") is None


# ── Migração legada ───────────────────────────────────────────────────────────

def test_migracao_preserva_dados(tmp_path, monkeypatch):
    import sqlite3
    db_path = tmp_path / "legacy.db"
    monkeypatch.setattr(database, "DB_NAME", str(db_path))
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE pacientes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL,
                telefone TEXT, cpf TEXT, cns TEXT, data_nascimento TEXT,
                sexo TEXT, bairro TEXT, peso REAL, altura REAL,
                pressao TEXT, imc REAL, classificacao_imc TEXT
            );
            CREATE TABLE usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL,
                usuario TEXT UNIQUE NOT NULL, senha_hash TEXT NOT NULL,
                perfil TEXT NOT NULL, registro_profissional TEXT
            );
            INSERT INTO pacientes (id, nome) VALUES (41, 'Paciente Legado');
            INSERT INTO usuarios (id, nome, usuario, senha_hash, perfil)
            VALUES (1, 'Admin Legado', 'admin', 'hash-legado', 'ADMIN');
        """)
    database.criar_tabelas()
    with database.conectar() as conn:
        p = conn.execute("SELECT nome FROM pacientes WHERE id=41").fetchone()
        u = conn.execute("SELECT senha_hash FROM usuarios WHERE id=1").fetchone()
    assert p["nome"] == "Paciente Legado"
    assert u["senha_hash"] == "hash-legado"


# ── PWA ───────────────────────────────────────────────────────────────────────

def test_manifest_acessivel(client):
    r = client.get("/manifest.json")
    assert r.status_code == 200
    assert b"DV Saude" in r.data


def test_service_worker_acessivel(client):
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert b"Service Worker" in r.data


def test_offline_page_retorna_503(client):
    r = client.get("/offline")
    assert r.status_code == 503


def test_cadastro_paciente_retorna_id_e_listagem_paginada(client):
    patient_id = database.adicionar_paciente("Paciente de Teste", cpf="00000000191")
    assert isinstance(patient_id, int)
    patients, total = database.list_pacientes_paginado(page=1, page_size=1)
    assert total == 1
    assert patients[0]["id"] == patient_id


def test_organizacao_por_cnes_auditoria_e_versoes_de_documentos(client):
    dentist_created = database.cadastrar_usuario(
        "Profissional Teste", "dentista-tenant", "DENTISTA", "CRO-TESTE", "1234567"
    )
    assert dentist_created
    dentist = database.buscar_usuario_por_login("dentista-tenant")
    patient_id = database.adicionar_paciente("Paciente Fictício", cnes_unidade="1234567")
    conn = database.conectar()
    try:
        unit = conn.execute(
            "SELECT organization_id FROM dv_unidades WHERE cnes=?", ("1234567",)
        ).fetchone()
        patient = conn.execute(
            "SELECT organization_id FROM pacientes WHERE id=?", (patient_id,)
        ).fetchone()
        professional = conn.execute(
            "SELECT organization_id FROM usuarios WHERE id=?", (dentist["id"],)
        ).fetchone()
        assert unit["organization_id"] == patient["organization_id"]
        assert unit["organization_id"] == professional["organization_id"]
    finally:
        conn.close()

    assert database.adicionar_arquivo(
        patient_id, "Exame", "exame.pdf", "stored-v1.pdf", "application/pdf",
        cnes_unidade="1234567", sha256="a" * 64, actor_user_id=dentist["id"],
    )
    file_record = database.listar_arquivos_paciente(patient_id, "1234567")[0]
    version = database.registrar_versao_documento(
        file_record["id"], "stored-v2.pdf", "b" * 64, "application/pdf",
        actor_user_id=dentist["id"], cnes_unidade="1234567",
    )
    assert version == 2
    database.registrar_auditoria(
        dentist["id"], "patient_view", "patient", patient_id, "request-test"
    )
    conn = database.conectar()
    try:
        versions = conn.execute(
            "SELECT version_number, storage_key, sha256 FROM document_versions WHERE arquivo_id=? ORDER BY version_number",
            (file_record["id"],),
        ).fetchall()
        event = conn.execute(
            "SELECT action, resource_type, resource_id, request_id FROM audit_events WHERE request_id=?",
            ("request-test",),
        ).fetchone()
        assert [item["version_number"] for item in versions] == [1, 2]
        assert versions[0]["sha256"] == "a" * 64
        assert versions[1]["storage_key"] == "stored-v2.pdf"
        assert dict(event) == {
            "action": "patient_view",
            "resource_type": "patient",
            "resource_id": str(patient_id),
            "request_id": "request-test",
        }
    finally:
        conn.close()
