"""Portal e módulos administrativos do DV Saúde."""

import secrets
import sqlite3
import threading
from datetime import date, timedelta
from functools import wraps
from typing import Any, Callable

import database
from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)
from markupsafe import Markup, escape

dv_saude = Blueprint("dv_saude", __name__)
_schema_initialized = False
_schema_lock = threading.Lock()

PHARMACY_ROLES = {"ADMIN", "FARMACIA", "FARMÁCIA", "FARMACEUTICO", "FARMACÊUTICO"}
CLINICAL_ROLES = {
    "ADMIN",
    "DENTISTA",
    "MEDICO",
    "MÉDICO",
    "ENFERMEIRO",
    "ENFERMEIRA",
    "PSICOLOGO",
    "PSICÓLOGO",
}


def role_allows(module: str, profile: str | None) -> bool:
    role = (profile or "").strip().upper()
    if role == "ADMIN":
        return True
    if module == "clinical":
        return role in CLINICAL_ROLES
    if module == "pharmacy":
        return role in PHARMACY_ROLES
    return False


def _require(module: str) -> Callable:
    def decorate(view: Callable) -> Callable:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if not session.get("user_id"):
                return redirect(url_for("login", next=request.url))
            if not role_allows(module, session.get("user_perfil")):
                abort(403)
            if session.get("user_perfil") != "ADMIN" and not session.get("cnes_unidade"):
                abort(403, description="A conta nao esta vinculada a uma unidade CNES.")
            return view(*args, **kwargs)

        return wrapped

    return decorate


def _connect() -> Any:
    conn = database.conectar()
    try:
        conn.execute("PRAGMA foreign_keys = ON")
    except Exception:
        pass
    return conn


def _active_cnes() -> str | None:
    return None if session.get("user_perfil") == "ADMIN" else session.get("cnes_unidade")


def _stored_unit_name(unit: str, cnes: str | None) -> str:
    if cnes:
        return f"{cnes}::{unit}"
    return unit


def _display_unit_name(unit: str, cnes: str | None) -> str:
    prefix = f"{cnes}::" if cnes else ""
    return unit[len(prefix):] if prefix and unit.startswith(prefix) else unit


def _ensure_schema() -> None:
    conn = _connect()
    try:
        if database._USE_POSTGRES:
            conn.execute("SELECT pg_advisory_xact_lock(731904262)")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS dv_medicamentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                principio_ativo TEXT NOT NULL DEFAULT '',
                apresentacao TEXT NOT NULL DEFAULT '',
                unidade_medida TEXT NOT NULL,
                estoque_minimo INTEGER NOT NULL DEFAULT 0 CHECK (estoque_minimo >= 0),
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_dv_medicamentos_nome
                ON dv_medicamentos(nome);
            CREATE TABLE IF NOT EXISTS dv_farmacia_lotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                medicamento_id INTEGER NOT NULL REFERENCES dv_medicamentos(id),
                unidade TEXT NOT NULL,
                cnes_unidade TEXT,
                lote TEXT NOT NULL,
                fornecedor TEXT,
                validade TEXT NOT NULL,
                saldo INTEGER NOT NULL DEFAULT 0 CHECK (saldo >= 0),
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (medicamento_id, unidade, lote)
            );
            CREATE INDEX IF NOT EXISTS idx_dv_lotes_unidade_validade
                ON dv_farmacia_lotes(unidade, validade);
            CREATE TABLE IF NOT EXISTS dv_farmacia_movimentacoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chave_operacao TEXT NOT NULL UNIQUE,
                medicamento_id INTEGER NOT NULL REFERENCES dv_medicamentos(id),
                lote_id INTEGER NOT NULL REFERENCES dv_farmacia_lotes(id),
                unidade TEXT NOT NULL,
                cnes_unidade TEXT,
                tipo TEXT NOT NULL CHECK (
                    tipo IN ('ENTRADA', 'SAIDA', 'DISPENSACAO', 'AJUSTE_ENTRADA', 'AJUSTE_SAIDA')
                ),
                quantidade INTEGER NOT NULL CHECK (quantidade > 0),
                fornecedor TEXT,
                justificativa TEXT NOT NULL,
                usuario_id INTEGER NOT NULL,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_dv_movimentacoes_criado
                ON dv_farmacia_movimentacoes(criado_em);
            CREATE TABLE IF NOT EXISTS organizacoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER,
                actor_user_id INTEGER,
                action TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT,
                request_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS dv_unidades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cnes TEXT NOT NULL UNIQUE,
                nome TEXT NOT NULL,
                organization_id INTEGER,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        unit_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(dv_unidades)").fetchall()
        }
        if "organization_id" not in unit_columns:
            conn.execute("ALTER TABLE dv_unidades ADD COLUMN organization_id INTEGER")
        for unit in conn.execute(
            "SELECT cnes, nome, organization_id FROM dv_unidades ORDER BY cnes"
        ).fetchall():
            if unit["organization_id"] is None:
                slug = f"cnes-{unit['cnes']}"
                organization = conn.execute(
                    "SELECT id FROM organizacoes WHERE slug=?", (slug,)
                ).fetchone()
                if organization:
                    organization_id = organization["id"]
                else:
                    organization_id = conn.execute(
                        "INSERT INTO organizacoes(nome, slug) VALUES (?, ?)",
                        (unit["nome"], slug),
                    ).lastrowid
                conn.execute(
                    "UPDATE dv_unidades SET organization_id=? WHERE cnes=?",
                    (organization_id, unit["cnes"]),
                )
        for table in ("dv_farmacia_lotes", "dv_farmacia_movimentacoes"):
            columns = {
                row["name"]
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if "cnes_unidade" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN cnes_unidade TEXT")
            if "organization_id" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN organization_id INTEGER")
            if table == "dv_farmacia_lotes" and "fornecedor" not in columns:
                conn.execute("ALTER TABLE dv_farmacia_lotes ADD COLUMN fornecedor TEXT")
            if table == "dv_farmacia_movimentacoes" and "fornecedor" not in columns:
                conn.execute("ALTER TABLE dv_farmacia_movimentacoes ADD COLUMN fornecedor TEXT")
            conn.execute(
                f"""UPDATE {table} SET organization_id=(
                       SELECT u.organization_id FROM dv_unidades u
                       WHERE u.cnes={table}.cnes_unidade
                   ) WHERE organization_id IS NULL AND cnes_unidade IS NOT NULL"""
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dv_lotes_cnes ON dv_farmacia_lotes(cnes_unidade)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dv_mov_cnes ON dv_farmacia_movimentacoes(cnes_unidade)"
        )
        conn.commit()
    finally:
        conn.close()


@dv_saude.before_request
def initialize_module_tables() -> None:
    global _schema_initialized
    if _schema_initialized:
        return
    with _schema_lock:
        if not _schema_initialized:
            _ensure_schema()
            _schema_initialized = True


def _csrf_token() -> str:
    token = session.get("_dv_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_dv_csrf"] = token
    return token


def _validate_csrf() -> None:
    submitted = request.form.get("csrf_token", "")
    expected = session.get("_dv_csrf", "")
    if not expected or not secrets.compare_digest(submitted, expected):
        abort(400, description="Token de segurança inválido. Atualize a página e tente novamente.")


def _page(title: str, content: str) -> str:
    base = current_app.extensions.get("dv_saude_base")
    if base is None:
        raise RuntimeError("O layout compartilhado do DV Saúde não foi configurado.")
    return render_template_string(
        base(), titulo=title, conteudo=Markup(content), csrf_token=_csrf_token()
    )


def _module_links(active: str) -> str:
    links = [
        ("medicamentos", "Medicamentos", "/farmacia-sus/medicamentos/"),
        ("estoque", "Estoque e lotes", "/farmacia-sus/estoque/"),
        ("movimentacoes", "Movimentações", "/farmacia-sus/movimentacoes/"),
        ("dispensacoes", "Dispensações", "/farmacia-sus/dispensacoes/"),
        ("relatorios", "Relatórios", "/farmacia-sus/relatorios/"),
    ]
    return "".join(
        f'<a class="subnav-link {"selected" if key == active else ""}" href="{href}">{label}</a>'
        for key, label, href in links
    )


def _pharmacy_nav(active: str) -> str:
    return f'<nav class="subnav" aria-label="Navegação da Farmácia SUS">{_module_links(active)}</nav>'


def _count(conn: sqlite3.Connection, table: str) -> int:
    allowed = {"pacientes", "agenda", "prontuario_soap", "usuarios", "dv_medicamentos"}
    if table not in allowed:
        raise ValueError("Tabela inválida.")
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


@dv_saude.route("/prontuario/")
@_require("clinical")
def prontuario_dashboard() -> Any:
    return redirect(url_for("prontuario_page"))


@dv_saude.route("/odonto/")
@_require("clinical")
def odonto_dashboard() -> str:
    content = """
    <section class="hero"><p class="eyebrow">MÓDULO ODONTOLÓGICO</p>
      <h1>DV Odonto</h1><p>Odontograma, pacientes e rotina clínica em um só lugar.</p></section>
    <section class="cards">
      <a class="module-card" href="/pacientes"><span class="icon">👥</span><h2>Pacientes</h2><p>Cadastros e dados clínicos disponíveis.</p></a>
      <a class="module-card" href="/odontograma"><span class="icon">🦷</span><h2>Odontograma</h2><p>Arcadas e condições por dente e face.</p></a>
      <a class="module-card" href="/agenda"><span class="icon">📅</span><h2>Agenda</h2><p>Consultas e lembretes configurados.</p></a>
      <a class="module-card" href="/documentos"><span class="icon">📄</span><h2>Documentos</h2><p>Arquivos e termos do atendimento.</p></a>
      <a class="module-card" href="/receita"><span class="icon">🧾</span><h2>Receituário</h2><p>Geração de documentos odontológicos.</p></a>
    </section>
    """
    return _page("DV Odonto — DV Saúde", content)


@dv_saude.route("/farmacia-sus/")
@_require("pharmacy")
def pharmacy_dashboard() -> str:
    conn = _connect()
    try:
        cnes = _active_cnes()
        medicine_count = _count(conn, "dv_medicamentos")
        stock_total = conn.execute(
            "SELECT COALESCE(SUM(saldo), 0) FROM dv_farmacia_lotes "
            "WHERE (? IS NULL OR cnes_unidade=?)",
            (cnes, cnes),
        ).fetchone()[0]
        low_stock = conn.execute(
            """
            SELECT COUNT(*) FROM dv_medicamentos m
            WHERE COALESCE((SELECT SUM(l.saldo) FROM dv_farmacia_lotes l
                WHERE l.medicamento_id=m.id AND (? IS NULL OR l.cnes_unidade=?)), 0) <= m.estoque_minimo
            """,
            (cnes, cnes),
        ).fetchone()[0]
        expiry = conn.execute(
            "SELECT COUNT(*) FROM dv_farmacia_lotes WHERE saldo > 0 AND validade <= ? "
            "AND (? IS NULL OR cnes_unidade=?)",
            (date.today().isoformat(), cnes, cnes),
        ).fetchone()[0]
        expiry_soon = conn.execute(
            "SELECT COUNT(*) FROM dv_farmacia_lotes WHERE saldo > 0 AND validade > ? "
            "AND validade <= ? AND (? IS NULL OR cnes_unidade=?)",
            (date.today().isoformat(), (date.today() + timedelta(days=30)).isoformat(), cnes, cnes),
        ).fetchone()[0]
    finally:
        conn.close()
    content = f"""
      {_pharmacy_nav("dashboard")}
      <section class="hero"><p class="eyebrow">ASSISTÊNCIA FARMACÊUTICA</p><h1>DV Farmácia SUS</h1>
        <p>Controle local de medicamentos, lotes e movimentações. Sem conectores oficiais configurados.</p></section>
      <section class="stats">
        <article class="stat"><span>Medicamentos</span><strong>{medicine_count}</strong></article>
        <article class="stat"><span>Unidades em estoque</span><strong>{int(stock_total)}</strong></article>
        <article class="stat"><span>Abaixo do mínimo</span><strong>{int(low_stock)}</strong></article>
        <article class="stat"><span>Lotes vencidos</span><strong>{int(expiry)}</strong></article>
        <article class="stat"><span>Validade em até 30 dias</span><strong>{int(expiry_soon)}</strong></article>
      </section>
      <p class="notice">O estoque e o histórico são filtrados pela unidade CNES da conta.</p>
    """
    return _page("DV Farmácia SUS — DV Saúde", content)


@dv_saude.route("/farmacia-sus/medicamentos/", methods=["GET", "POST"])
@_require("pharmacy")
def pharmacy_medicines() -> Any:
    message = ""
    if request.method == "POST":
        _validate_csrf()
        name = request.form.get("nome", "").strip()
        active = request.form.get("principio_ativo", "").strip()
        presentation = request.form.get("apresentacao", "").strip()
        unit = request.form.get("unidade_medida", "").strip()
        try:
            minimum = int(request.form.get("estoque_minimo", "0"))
        except ValueError:
            minimum = -1
        if not name or not unit or len(name) > 160 or len(unit) > 40 or minimum < 0:
            message = "Informe nome, unidade válida e estoque mínimo não negativo."
        else:
            conn = _connect()
            try:
                conn.execute(
                    """INSERT INTO dv_medicamentos
                    (nome, principio_ativo, apresentacao, unidade_medida, estoque_minimo)
                    VALUES (?, ?, ?, ?, ?)""",
                    (name, active, presentation, unit, minimum),
                )
                conn.commit()
                return redirect(url_for("dv_saude.pharmacy_medicines"))
            except database.PG_INTEGRITY_ERRORS:
                message = "Não foi possível cadastrar o medicamento. Verifique os dados informados."
            finally:
                conn.close()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, nome, principio_ativo, apresentacao, unidade_medida, estoque_minimo "
            "FROM dv_medicamentos ORDER BY nome"
        ).fetchall()
    finally:
        conn.close()
    table = "".join(
        f"<tr><td>{r['id']}</td><td>{escape(r['nome'])}</td><td>{escape(r['principio_ativo'])}</td>"
        f"<td>{escape(r['apresentacao'])}</td><td>{escape(r['unidade_medida'])}</td>"
        f"<td>{r['estoque_minimo']}</td></tr>"
        for r in rows
    ) or '<tr><td colspan="6" class="empty">Nenhum medicamento cadastrado.</td></tr>'
    content = f"""
      {_pharmacy_nav("medicamentos")}<section class="hero"><h1>Medicamentos</h1>
      <p>Cadastros locais; não representam catálogo oficial.</p></section>
      <form class="panel form-grid" method="post"><input type="hidden" name="csrf_token" value="{escape(_csrf_token())}">
        <label>Nome<input name="nome" required maxlength="160"></label>
        <label>Princípio ativo<input name="principio_ativo" maxlength="160"></label>
        <label>Apresentação<input name="apresentacao" maxlength="160"></label>
        <label>Unidade de medida<input name="unidade_medida" required maxlength="40" placeholder="comprimido, frasco..."></label>
        <label>Estoque mínimo<input type="number" name="estoque_minimo" min="0" value="0" required></label>
        <button class="button" type="submit">Cadastrar medicamento</button>
      </form>
      {f'<p class="error">{escape(message)}</p>' if message else ''}
      <div class="table-wrap"><table><thead><tr><th>ID</th><th>Medicamento</th><th>Princípio ativo</th>
      <th>Apresentação</th><th>Unidade</th><th>Mínimo</th></tr></thead><tbody>{table}</tbody></table></div>
    """
    return _page("Medicamentos — Farmácia SUS", content)


def _movement_form(kind: str, heading: str, active: str) -> str:
    conn = _connect()
    try:
        meds = conn.execute("SELECT id, nome, unidade_medida FROM dv_medicamentos ORDER BY nome").fetchall()
        assigned_cnes = _active_cnes()
        if assigned_cnes:
            units = conn.execute(
                "SELECT cnes, nome FROM dv_unidades WHERE cnes=? ORDER BY nome", (assigned_cnes,)
            ).fetchall()
        else:
            units = conn.execute("SELECT cnes, nome FROM dv_unidades ORDER BY nome").fetchall()
    finally:
        conn.close()
    medicine_options = "".join(
        f'<option value="{r["id"]}">{escape(r["nome"])} ({escape(r["unidade_medida"])})</option>'
        for r in meds
    )
    unit_options = "".join(
        f'<option value="{escape(r["cnes"])}">{escape(r["nome"])} — CNES {escape(r["cnes"])}</option>'
        for r in units
    )
    unit_input = (
        f'<select name="cnes_unidade" required><option value="">Selecione a unidade</option>{unit_options}</select>'
        if units
        else '<span role="status">Cadastre uma unidade CNES antes de movimentar estoque.</span>'
    )
    supplier_field = '<label>Fornecedor<input name="fornecedor" maxlength="160" autocomplete="organization"></label>' if kind == "ENTRADA" else ""
    if kind == "ENTRADA":
        type_options = '<option value="ENTRADA">Entrada</option><option value="AJUSTE_ENTRADA">Ajuste de entrada</option>'
    elif kind == "SAIDA":
        type_options = '<option value="SAIDA">Saída</option><option value="AJUSTE_SAIDA">Ajuste de saída</option>'
    else:
        type_options = ""
    movement_type = (
        f'<label>Tipo de operação<select name="tipo" required>{type_options}</select></label>'
        if type_options
        else ""
    )
    return f"""
      {_pharmacy_nav(active)}<section class="hero"><h1>{heading}</h1>
      <p>Movimentações registradas em transação e preservadas no histórico.</p></section>
      <p class="notice">Operações são restritas a administradores/farmacêuticos. Dispensações são registradas manualmente e não verificam prescrição clínica ou elegibilidade. Confirme a unidade antes de registrar.</p>
      <form class="panel form-grid" method="post">
        <input type="hidden" name="csrf_token" value="{escape(_csrf_token())}">
        <input type="hidden" name="chave_operacao" value="{secrets.token_urlsafe(24)}">
        <label>Medicamento<select name="medicamento_id" required><option value="">Selecione</option>{medicine_options}</select></label>
        <label>Unidade{unit_input}</label>
        {supplier_field}
        <label>Lote<input name="lote" required maxlength="80"></label>
        <label>Validade<input type="date" name="validade" required></label>
        <label>Quantidade<input type="number" name="quantidade" min="1" required></label>
        {movement_type}
        <label>Justificativa<input name="justificativa" maxlength="240" required></label>
        <button class="button" type="submit">Registrar movimentação</button>
      </form>
    """


def _save_movement(kind: str) -> str | None:
    _validate_csrf()
    requested_kind = request.form.get("tipo", kind)
    allowed_types = {
        "ENTRADA": {"ENTRADA", "AJUSTE_ENTRADA"},
        "SAIDA": {"SAIDA", "AJUSTE_SAIDA"},
        "DISPENSACAO": {"DISPENSACAO"},
    }
    if requested_kind not in allowed_types[kind]:
        return "Tipo de movimentação não permitido nesta operação."
    kind = requested_kind
    try:
        medication_id = int(request.form.get("medicamento_id", ""))
        quantity = int(request.form.get("quantidade", ""))
        expiry = date.fromisoformat(request.form.get("validade", ""))
    except (ValueError, TypeError):
        return "Selecione medicamento e validade válidos e informe quantidade inteira."
    lot = request.form.get("lote", "").strip()
    submitted_cnes = request.form.get("cnes_unidade", "").strip()
    supplier = request.form.get("fornecedor", "").strip()
    reason = request.form.get("justificativa", "").strip()
    operation_key = request.form.get("chave_operacao", "").strip()
    if (
        quantity <= 0
        or not lot
        or len(lot) > 80
        or not submitted_cnes
        or len(submitted_cnes) != 7
        or not submitted_cnes.isdigit()
        or not reason
        or len(reason) > 240
        or len(supplier) > 160
        or not operation_key
        or len(operation_key) > 100
    ):
        return "Revise quantidade, lote, unidade e justificativa."
    if kind in {"SAIDA", "DISPENSACAO", "AJUSTE_SAIDA"} and expiry < date.today():
        return "Não é permitido retirar lote vencido."
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        assigned_cnes = _active_cnes()
        cnes = assigned_cnes or submitted_cnes
        if assigned_cnes and submitted_cnes != assigned_cnes:
            conn.rollback()
            return "A unidade selecionada nao pertence a esta conta."
        unit_record = conn.execute(
            "SELECT nome, organization_id FROM dv_unidades WHERE cnes=?", (cnes,)
        ).fetchone()
        if not unit_record:
            conn.rollback()
            return "Selecione uma unidade CNES cadastrada."
        unit = _stored_unit_name(unit_record["nome"], cnes)
        organization_id = unit_record["organization_id"]
        medicine = conn.execute(
            "SELECT id FROM dv_medicamentos WHERE id=?", (medication_id,)
        ).fetchone()
        if not medicine:
            conn.rollback()
            return "Medicamento não encontrado."
        row = conn.execute(
            """SELECT id, saldo, validade, fornecedor FROM dv_farmacia_lotes
            WHERE medicamento_id=? AND unidade=? AND lote=?
              AND (cnes_unidade=? OR (cnes_unidade IS NULL AND ? IS NULL))""",
            (medication_id, unit, lot, cnes, cnes),
        ).fetchone()
        if kind in {"ENTRADA", "AJUSTE_ENTRADA"}:
            if row and row["validade"] != expiry.isoformat():
                conn.rollback()
                return "A validade informada diverge do lote já cadastrado."
            if row:
                lot_id = row["id"]
                conn.execute(
                    """UPDATE dv_farmacia_lotes SET saldo=saldo+?,
                       fornecedor=CASE WHEN ?<>'' THEN ? ELSE fornecedor END WHERE id=?""",
                    (quantity, supplier, supplier, lot_id),
                )
            else:
                cursor = conn.execute(
                    """INSERT INTO dv_farmacia_lotes
                    (medicamento_id, unidade, cnes_unidade, organization_id, lote, fornecedor, validade, saldo)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (medication_id, unit, cnes, organization_id, lot, supplier or None, expiry.isoformat(), quantity),
                )
                lot_id = cursor.lastrowid
            movement_supplier = supplier or (row["fornecedor"] if row else None)
        else:
            if not row or row["validade"] != expiry.isoformat():
                conn.rollback()
                return "Lote não encontrado nesta unidade ou validade incorreta."
            if row["saldo"] < quantity:
                conn.rollback()
                return "Saldo insuficiente para esta saída."
            lot_id = row["id"]
            movement_supplier = row["fornecedor"]
            conn.execute(
                "UPDATE dv_farmacia_lotes SET saldo=saldo-? WHERE id=?",
                (quantity, lot_id),
            )
        conn.execute(
            """INSERT INTO dv_farmacia_movimentacoes
            (chave_operacao, medicamento_id, lote_id, unidade, cnes_unidade, organization_id, tipo,
             quantidade, fornecedor, justificativa, usuario_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                operation_key,
                medication_id,
                lot_id,
                unit,
                cnes,
                organization_id,
                kind,
                quantity,
                movement_supplier,
                reason,
                int(session["user_id"]),
            ),
        )
        conn.execute(
            """INSERT INTO audit_events
               (organization_id, actor_user_id, action, resource_type, resource_id, request_id)
               VALUES (?, ?, 'pharmacy_movement', 'pharmacy_movement', ?, ?)""",
            (organization_id, int(session["user_id"]), operation_key, request.environ.get("dv_request_id")),
        )
        conn.commit()
        return None
    except database.PG_INTEGRITY_ERRORS:
        conn.rollback()
        return "Operação duplicada ou inválida; atualize a página e confira o histórico."
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@dv_saude.route("/farmacia-sus/estoque/", methods=["GET", "POST"])
@_require("pharmacy")
def pharmacy_stock() -> Any:
    message = None
    if request.method == "POST":
        message = _save_movement("ENTRADA")
        if message is None:
            return redirect(url_for("dv_saude.pharmacy_stock"))
    conn = _connect()
    try:
        cnes = _active_cnes()
        rows = conn.execute(
            """SELECT m.nome, l.unidade, l.lote, l.fornecedor, l.validade, l.saldo, m.unidade_medida
            FROM dv_farmacia_lotes l JOIN dv_medicamentos m ON m.id=l.medicamento_id
            WHERE (? IS NULL OR l.cnes_unidade=?)
            ORDER BY l.validade, m.nome""",
            (cnes, cnes),
        ).fetchall()
    finally:
        conn.close()
    table = "".join(
        f"<tr><td>{escape(r['nome'])}</td><td>{escape(_display_unit_name(r['unidade'], _active_cnes()))}</td><td>{escape(r['lote'])}</td>"
        f"<td>{escape(r['fornecedor'] or '')}</td><td>{escape(r['validade'])}</td><td>{r['saldo']} {escape(r['unidade_medida'])}</td></tr>"
        for r in rows
    ) or '<tr><td colspan="5" class="empty">Nenhum lote registrado.</td></tr>'
    return _page(
        "Estoque — Farmácia SUS",
        _movement_form("ENTRADA", "Estoque e lotes — registrar entrada", "estoque")
        + (f'<p class="error">{escape(message)}</p>' if message else "")
        + f'<div class="table-wrap"><table><thead><tr><th>Medicamento</th><th>Unidade</th><th>Lote</th><th>Fornecedor</th><th>Validade</th><th>Saldo</th></tr></thead><tbody>{table}</tbody></table></div>',
    )


def _movement_listing(active: str, heading: str, kind: str) -> str:
    message = _save_movement(kind) if request.method == "POST" else None
    if request.method == "POST" and message is None:
        endpoint = (
            "dv_saude.pharmacy_dispensations"
            if kind == "DISPENSACAO"
            else "dv_saude.pharmacy_movements"
        )
        return redirect(url_for(endpoint))
    conn = _connect()
    try:
        cnes = _active_cnes()
        rows = conn.execute(
            """SELECT x.criado_em, m.nome, x.unidade, l.lote, x.fornecedor, x.tipo, x.quantidade,
            x.justificativa, x.usuario_id
            FROM dv_farmacia_movimentacoes x
            JOIN dv_medicamentos m ON m.id=x.medicamento_id
            JOIN dv_farmacia_lotes l ON l.id=x.lote_id
            WHERE (? IS NULL OR x.cnes_unidade=?)
            ORDER BY x.id DESC LIMIT 200"""
            , (cnes, cnes)
        ).fetchall()
    finally:
        conn.close()
    table = "".join(
        f"<tr><td>{escape(r['criado_em'])}</td><td>{escape(r['nome'])}</td>"
        f"<td>{escape(_display_unit_name(r['unidade'], _active_cnes()))}</td><td>{escape(r['lote'])}</td><td>{escape(r['fornecedor'] or '')}</td><td>{escape(r['tipo'])}</td>"
        f"<td>{r['quantidade']}</td><td>{escape(r['justificativa'])}</td></tr>"
        for r in rows
    ) or '<tr><td colspan="8" class="empty">Nenhuma movimentação registrada.</td></tr>'
    content = _movement_form(kind, heading, active)
    if message:
        content += f'<p class="error">{escape(message)}</p>'
    content += f"""
      <h2 class="section-title">Histórico recente</h2>
      <div class="table-wrap"><table><thead><tr><th>Data/hora</th><th>Medicamento</th><th>Unidade</th>
      <th>Lote</th><th>Fornecedor</th><th>Tipo</th><th>Quantidade</th><th>Justificativa</th></tr></thead><tbody>{table}</tbody></table></div>
    """
    return content


@dv_saude.route("/farmacia-sus/movimentacoes/", methods=["GET", "POST"])
@_require("pharmacy")
def pharmacy_movements() -> str:
    return _page(
        "Movimentações — Farmácia SUS",
        _movement_listing("movimentacoes", "Movimentações de estoque — saída ou ajuste", "SAIDA"),
    )


@dv_saude.route("/farmacia-sus/dispensacoes/", methods=["GET", "POST"])
@_require("pharmacy")
def pharmacy_dispensations() -> str:
    return _page(
        "Dispensações — Farmácia SUS",
        _movement_listing("dispensacoes", "Dispensar medicamento", "DISPENSACAO"),
    )


@dv_saude.route("/farmacia-sus/relatorios/")
@_require("pharmacy")
def pharmacy_reports() -> str:
    start = request.args.get("inicio", "")
    end = request.args.get("fim", "")
    try:
        if start:
            date.fromisoformat(start)
        if end:
            date.fromisoformat(end)
    except ValueError:
        abort(400, description="Informe datas válidas no formato AAAA-MM-DD.")
    if start and end and start > end:
        abort(400, description="A data inicial deve ser anterior à data final.")
    clauses = ["1=1"]
    values: list[str] = []
    cnes = _active_cnes()
    if cnes:
        clauses.append("x.cnes_unidade=?")
        values.append(cnes)
    if start:
        clauses.append("date(x.criado_em) >= date(?)")
        values.append(start)
    if end:
        clauses.append("date(x.criado_em) <= date(?)")
        values.append(end)
    conn = _connect()
    try:
        rows = conn.execute(
            f"""SELECT x.unidade, x.tipo, COUNT(*) AS operacoes, SUM(x.quantidade) AS quantidade
            FROM dv_farmacia_movimentacoes x WHERE {' AND '.join(clauses)}
            GROUP BY x.unidade, x.tipo ORDER BY x.unidade, x.tipo""",
            values,
        ).fetchall()
    finally:
        conn.close()
    table = "".join(
        f"<tr><td>{escape(_display_unit_name(r['unidade'], cnes))}</td><td>{escape(r['tipo'])}</td>"
        f"<td>{r['operacoes']}</td><td>{r['quantidade']}</td></tr>"
        for r in rows
    ) or '<tr><td colspan="4" class="empty">Nenhuma movimentação no período.</td></tr>'
    content = f"""
      {_pharmacy_nav("relatorios")}<section class="hero"><h1>Relatórios de estoque</h1>
      <p>Totais agregados das movimentações registradas localmente.</p></section>
      <form class="panel form-grid" method="get">
        <label>Início<input type="date" name="inicio" value="{escape(start)}"></label>
        <label>Fim<input type="date" name="fim" value="{escape(end)}"></label>
        <button class="button" type="submit">Aplicar filtros</button>
      </form>
      <div class="table-wrap"><table><thead><tr><th>Unidade</th><th>Tipo</th><th>Operações</th>
      <th>Quantidade</th></tr></thead><tbody>{table}</tbody></table></div>
    """
    return _page("Relatórios — Farmácia SUS", content)


@dv_saude.route("/gestao-publica/", methods=["GET", "POST"])
@_require("admin")
def public_management() -> Any:
    message = ""
    if request.method == "POST":
        _validate_csrf()
        if request.form.get("action") == "create_organization":
            try:
                database.criar_organizacao_e_vincular_unidade(
                    request.form.get("organization_name", ""),
                    request.form.get("organization_cnes", ""),
                    session.get("user_id"), request.environ.get("dv_request_id"),
                    request.form.get("organization_id") or None,
                )
                return redirect(url_for("dv_saude.public_management"))
            except ValueError as exc:
                message = str(exc)
        else:
            cnes = request.form.get("cnes", "").strip()
            name = request.form.get("nome", "").strip()
            if not cnes.isdigit() or len(cnes) != 7 or not name or len(name) > 160:
                message = "Informe CNES com 7 dígitos e nome de unidade válido."
            else:
                conn = _connect()
                try:
                    conn.execute("INSERT INTO dv_unidades(cnes, nome) VALUES (?, ?)", (cnes, name))
                    conn.commit()
                    database.garantir_organizacao_unidade(cnes, name)
                    database.registrar_auditoria(
                        session.get("user_id"), "admin_unit_create", "unit", cnes,
                        request.environ.get("dv_request_id"),
                    )
                    return redirect(url_for("dv_saude.public_management"))
                except database.PG_INTEGRITY_ERRORS:
                    message = "CNES já cadastrado."
                finally:
                    conn.close()
    conn = _connect()
    try:
        units = conn.execute("SELECT cnes, nome, organization_id FROM dv_unidades ORDER BY nome").fetchall()
        organizations = database.listar_organizacoes()
        professionals = conn.execute(
            "SELECT nome, usuario, perfil, registro_profissional FROM usuarios ORDER BY nome"
        ).fetchall()
        patient_count = _count(conn, "pacientes")
        appointment_count = _count(conn, "agenda")
        soap_count = _count(conn, "prontuario_soap")
    finally:
        conn.close()
    unit_rows = "".join(
        f"<tr><td>{escape(r['cnes'])}</td><td>{escape(r['nome'])}</td><td>{escape(next((o['nome'] for o in organizations if o['id'] == r['organization_id']), ''))}</td></tr>" for r in units
    ) or '<tr><td colspan="3" class="empty">Nenhuma unidade cadastrada nesta plataforma.</td></tr>'
    pro_rows = "".join(
        f"<tr><td>{escape(r['nome'])}</td><td>{escape(r['perfil'])}</td>"
        f"<td>{escape(r['registro_profissional'])}</td></tr>"
        for r in professionals
    ) or '<tr><td colspan="3" class="empty">Nenhum profissional cadastrado.</td></tr>'
    content = f"""
      <section class="hero"><p class="eyebrow">ADMINISTRAÇÃO</p><h1>DV Gestão Pública</h1>
      <p>Painel administrativo com dados registrados localmente.</p></section>
      <p class="notice">Organizações agrupam unidades para gestão. O acesso clínico continua isolado pelo CNES associado à conta.</p>
      <section class="stats"><article class="stat"><span>Pacientes cadastrados</span><strong>{patient_count}</strong></article>
      <article class="stat"><span>Consultas cadastradas</span><strong>{appointment_count}</strong></article>
      <article class="stat"><span>Evoluções SOAP</span><strong>{soap_count}</strong></article></section>
      <h2 class="section-title">Unidades registradas</h2>
      <form class="panel form-grid" method="post"><input type="hidden" name="csrf_token" value="{escape(_csrf_token())}">
      <label>CNES (7 dígitos)<input name="cnes" pattern="[0-9]{{7}}" maxlength="7" required></label>
      <label>Nome da unidade<input name="nome" maxlength="160" required></label>
      <button class="button" type="submit">Cadastrar unidade</button></form>
      <h3>Organizações e unidades</h3>
      <form class="panel form-grid" method="post"><input type="hidden" name="csrf_token" value="{escape(_csrf_token())}">
      <input type="hidden" name="action" value="create_organization">
      <label>Organização existente<select name="organization_id"><option value="">Criar nova organização</option>{''.join(f'<option value="{o["id"]}">{escape(o["nome"])}</option>' for o in organizations)}</select></label>
      <label>Nome para nova organização<input name="organization_name" maxlength="160"></label>
      <label>Unidade existente<select name="organization_cnes" required>{''.join(f'<option value="{escape(u["cnes"])}">{escape(u["nome"])} — CNES {escape(u["cnes"])}</option>' for u in units)}</select></label>
      <button class="button" type="submit">Vincular unidade à organização</button></form>
      {f'<p class="error">{escape(message)}</p>' if message else ''}
      <div class="table-wrap"><table><thead><tr><th>CNES</th><th>Unidade</th><th>Organização</th></tr></thead><tbody>{unit_rows}</tbody></table></div>
      <h2 class="section-title">Profissionais cadastrados</h2>
      <div class="table-wrap"><table><thead><tr><th>Nome</th><th>Perfil</th><th>Registro</th></tr></thead><tbody>{pro_rows}</tbody></table></div>
    """
    return _page("Gestão Pública — DV Saúde", content)


@dv_saude.route("/integracoes/")
@_require("admin")
def integrations() -> str:
    names = ["e-SUS APS", "LEDI APS", "RNDS", "Assistência farmacêutica (e-SUS AF / Hórus)"]
    statuses = "".join(
        f'<article class="module-card"><h2>{escape(name)}</h2>'
        '<p>Não configurada</p><small>Nenhum conector validado neste projeto.</small></article>'
        for name in names
    )
    content = f"""
      <section class="hero"><p class="eyebrow">INTEROPERABILIDADE</p><h1>DV Integrações</h1>
      <p>Estado comprovado dos conectores externos.</p></section>
      <section class="cards">{statuses}</section>
      <p class="notice">Não há endpoints, credenciais, logs de conectores nem configuração oficial implementada.</p>
    """
    return _page("Integrações — DV Saúde", content)


@dv_saude.route("/configuracoes/")
@_require("admin")
def settings_page() -> str:
    content = """
      <section class="hero"><p class="eyebrow">ADMINISTRAÇÃO</p><h1>Configurações</h1>
      <p>Configurações técnicas da instância e do acesso.</p></section>
      <section class="panel"><h2>Estado de segurança</h2>
      <p>As funções novas de Farmácia SUS e Gestão Pública exigem sessão autenticada.</p>
      <p>Permissões individuais por organização/unidade não existem no banco atual e permanecem pendentes.</p>
      <p>Integrações oficiais permanecem não configuradas.</p></section>
    """
    return _page("Configurações — DV Saúde", content)


@dv_saude.route("/busca/")
@_require("admin")
def global_search() -> str:
    term = request.args.get("q", "").strip()
    if len(term) > 80:
        abort(400, description="A busca deve conter no máximo 80 caracteres.")
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        abort(400, description="Página inválida.")
    if page < 1 or page > 10000:
        abort(400, description="Página inválida.")
    matches: list[str] = []
    total = 0
    if len(term) >= 2:
        conn = _connect()
        try:
            pattern = f"%{term}%"
            total = conn.execute(
                """SELECT
                    (SELECT COUNT(*) FROM pacientes WHERE nome LIKE ?) +
                    (SELECT COUNT(*) FROM dv_medicamentos WHERE nome LIKE ?)""",
                (pattern, pattern),
            ).fetchone()[0]
            results = conn.execute(
                """SELECT categoria, nome, destino FROM (
                    SELECT 'Paciente' AS categoria, nome,
                        '/prontuario?paciente_id=' || id AS destino
                    FROM pacientes WHERE nome LIKE ?
                    UNION ALL
                    SELECT 'Medicamento' AS categoria, nome,
                        '/farmacia-sus/medicamentos/' AS destino
                    FROM dv_medicamentos WHERE nome LIKE ?
                ) ORDER BY categoria, nome LIMIT 20 OFFSET ?""",
                (pattern, pattern, (page - 1) * 20),
            ).fetchall()
            matches.extend(
                f'<li><a href="{escape(r["destino"])}">{escape(r["categoria"])}: {escape(r["nome"])}</a></li>'
                for r in results
            )
        finally:
            conn.close()
    rows = "".join(matches) or '<li class="empty">Nenhum resultado encontrado.</li>'
    page_count = max(1, (total + 19) // 20)
    page_links = ""
    if total:
        previous = (
            f'<a href="{escape(url_for("dv_saude.global_search", q=term, page=page - 1))}">Anterior</a>'
            if page > 1
            else ""
        )
        following = (
            f'<a href="{escape(url_for("dv_saude.global_search", q=term, page=page + 1))}">Próxima</a>'
            if page < page_count
            else ""
        )
        page_links = (
            f'<nav class="subnav" aria-label="Paginação">{previous}'
            f'<span class="subnav-link">Página {page} de {page_count} — {total} resultados</span>'
            f'{following}</nav>'
        )
    content = f"""
      <section class="hero"><h1>Busca global</h1><p>Pesquisa limitada a pacientes e medicamentos autorizados para administradores.</p></section>
      <form class="panel search-form" method="get"><label for="global-q">Nome do paciente ou medicamento</label>
      <input id="global-q" type="search" name="q" maxlength="80" minlength="2" value="{escape(term)}">
      <button class="button" type="submit">Buscar</button></form><ul class="results">{rows}</ul>{page_links}
    """
    return _page("Busca global — DV Saúde", content)
