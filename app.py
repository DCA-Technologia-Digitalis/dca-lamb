import os
import sys

# ===================================================================
# 1. PREVENÇÃO DE TRAVAMENTOS NO WINDOWS (Python 3.14 / Locale / I/O)
# ===================================================================
sys.dont_write_bytecode = True
os.environ["PYTHONUNBUFFERED"] = "1"
os.environ["LC_ALL"] = "C"
os.environ["LANG"] = "C"

# ===================================================================
# 2. IMPORTAÇÕES PADRÃO DO PYTHON E DO FLASK
# ===================================================================
import io
import json
import os
import secrets
import uuid
import click
from datetime import date, datetime
from functools import wraps
from typing import Any
from urllib.parse import urlsplit

from flask import (
    abort,
    Flask,
    flash,
    redirect,
    render_template,
    render_template_string,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.utils import secure_filename
from markupsafe import Markup, escape

# ===================================================================
# 3. IMPORTAÇÕES DOS MÓDULOS LOCAIS (SisDCA)
# ===================================================================
import database
import gerador_pdf
from api import auth_bp

# Tenta importar módulos opcionais sem interromper a execução do servidor
try:
    import whatsapp
except ImportError:
    whatsapp = None

try:
    from viewer3d import VIEWER_3D
except ImportError:
    VIEWER_3D = ""

# ===================================================================
# 4. INICIALIZAÇÃO DA APLICAÇÃO E PASTAS
# ===================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
static_dir = os.path.join(BASE_DIR, 'static')
upload_dir = os.path.join(BASE_DIR, 'uploads')
UPLOAD_FOLDER = os.path.join(static_dir, 'uploads')

os.makedirs(static_dir, exist_ok=True)
os.makedirs(upload_dir, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__, static_folder=static_dir)

# Não use uma chave previsível para assinar cookies de sessão.
app.secret_key = os.environ.get("SECRET_KEY") or (
    secrets.token_hex(32) if "PORT" not in os.environ else None
)
if not app.secret_key:
    raise RuntimeError("Defina SECRET_KEY no ambiente antes de iniciar em produção.")
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "").lower() == "true",
)
app.register_blueprint(auth_bp)


def _auth_csrf_token() -> str:
    token = session.get("_auth_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_auth_csrf"] = token
    return token


def _validate_auth_csrf(submitted_token: str) -> bool:
    expected_token = session.get("_auth_csrf", "")
    return bool(expected_token) and secrets.compare_digest(expected_token, submitted_token)


@app.cli.command("recover-legacy-admin")
def recover_legacy_admin_command():
    """Interactively recover only the existing legacy bootstrap administrator."""
    password = click.prompt(
        "Nova senha do administrador (mínimo de 12 caracteres)",
        hide_input=True,
        confirmation_prompt=True,
    )
    if len(password) < 12:
        raise click.ClickException("A senha precisa ter pelo menos 12 caracteres.")

    backup_dir = (
        app.config.get("ADMIN_RECOVERY_BACKUP_DIR")
        or os.environ.get("ADMIN_RECOVERY_BACKUP_DIR")
        or os.path.join(BASE_DIR, "backups")
    )
    backup_path = database.backup_database(backup_dir)
    if not database.recover_legacy_admin_password(password):
        raise click.ClickException(
            "A conta admin não está no estado legado esperado; nenhuma conta foi alterada. "
            f"Backup preservado em {backup_path}."
        )
    click.echo(f"Senha atualizada somente para a conta admin legada. Backup: {backup_path}")


@app.cli.command("reset-admin-password")
def reset_admin_password_command():
    """Interactively reset the existing administrator password after verified backup."""
    password = click.prompt(
        "Nova senha do administrador (mínimo de 12 caracteres)",
        hide_input=True,
        confirmation_prompt=True,
    )
    if len(password) < 12:
        raise click.ClickException("A senha precisa ter pelo menos 12 caracteres.")

    backup_dir = (
        app.config.get("ADMIN_RECOVERY_BACKUP_DIR")
        or os.environ.get("ADMIN_RECOVERY_BACKUP_DIR")
        or os.path.join(BASE_DIR, "backups")
    )
    backup_path = database.backup_database(backup_dir)
    if not database.reset_admin_password(password):
        raise click.ClickException(
            "A conta ADMIN chamada admin não foi encontrada; nenhuma conta foi alterada. "
            f"Backup preservado em {backup_path}."
        )
    click.echo(f"Senha do administrador atualizada. Backup: {backup_path}")


@app.before_request
def protect_application_routes():
    public_endpoints = {"static", "index", "login", "logout", "swagger_json"}
    if (
        request.endpoint is not None
        and request.endpoint not in public_endpoints
        and not session.get("user_id")
    ):
        if request.path.startswith("/api/"):
            from flask import jsonify

            return jsonify({"status": "error", "mensagem": "Autenticação necessária."}), 401
        return redirect(url_for("login", next=request.url))

# ===================================================================
# 5. COMPONENTE REUTILIZÁVEL DE CABEÇALHO (NAVBAR COM LOGO)
# ===================================================================
NAVBAR_HEADER = """
<header style="padding: 15px 30px; display: flex; align-items: center; justify-content: space-between; border-bottom: 2px solid #1e3a8a; background-color: #f8fafc; font-family: 'Segoe UI', system-ui, sans-serif;">
    <div style="display: flex; align-items: center; gap: 15px;">
        <img src="/static/logo.png" alt="Logo SisDCA" style="height: 48px; width: auto; object-fit: contain;">
        <span style="font-size: 24px; font-weight: bold; color: #0f172a; letter-spacing: 0.5px;">
            Sis<span style="color: #2563eb;">DCA</span>
        </span>
    </div>
    
    <nav style="display: flex; gap: 20px; align-items: center;">
        <a href="/" style="color: #0f172a; text-decoration: none; font-weight: 500;">Início</a>
        <a href="/pacientes" style="color: #475569; text-decoration: none;">Pacientes</a>
        <a href="/prontuario" style="color: #475569; text-decoration: none;">Prontuário</a>
        <a href="/odontograma" style="color: #475569; text-decoration: none;">Odontograma</a>
        <a href="/agenda" style="color: #475569; text-decoration: none;">Agenda</a>
        <a href="/logout" style="color: #dc2626; text-decoration: none; margin-left: 10px;">Sair</a>
    </nav>
</header>
"""
# -------------------------------------------------------------------
# TABELA SIGTAP DO SUS (Procedimentos Odontológicos Oficiais)
# -------------------------------------------------------------------
PROCEDIMENTOS_SIGTAP = [
    {
        "codigo": "01.01.02.002-3",
        "nome": "Primeira Consulta Odontológica Programada",
        "categoria": "Diagnóstico",
    },
    {
        "codigo": "03.07.01.002-3",
        "nome": "Restauração em Dente Permanente (Face Simples)",
        "categoria": "Dentística",
    },
    {
        "codigo": "03.07.01.003-1",
        "nome": "Restauração em Dente Permanente (Composta)",
        "categoria": "Dentística",
    },
    {
        "codigo": "03.07.02.006-1",
        "nome": "Exodontia (Extração) de Dente Permanente",
        "categoria": "Cirurgia",
    },
    {
        "codigo": "03.07.01.001-5",
        "nome": "Raspagem e Alisamento Coronário (Limpeza)",
        "categoria": "Periodontia",
    },
    {
        "codigo": "03.07.02.003-7",
        "nome": "Tratamento Endodôntico (Canal)",
        "categoria": "Endodontia",
    },
    {
        "codigo": "01.01.02.001-5",
        "nome": "Atendimento Odontológico de Urgência",
        "categoria": "Urgência",
    },
]

from flask import jsonify
@app.route("/api/pacientes", methods=["GET", "POST"])
def api_pacientes():
    from dv_saude import role_allows

    if not role_allows("clinical", session.get("user_perfil")):
        from flask import jsonify

        return jsonify({"status": "error", "mensagem": "Perfil sem autorização clínica."}), 403
    if session.get("user_perfil") != "ADMIN" and not session.get("cnes_unidade"):
        return jsonify({"status": "error", "mensagem": "Conta sem unidade CNES vinculada."}), 403

    if request.method == "POST":

        dados = request.get_json(silent=True)

        if not dados:
            return jsonify({
                "status": "error",
                "mensagem": "JSON inválido ou vazio"
            }), 400

        nome = dados.get("nome")
        telefone = dados.get("telefone")
        cpf = dados.get("cpf")
        pressao = dados.get("pressao")

        if not nome:
            return jsonify({
                "status": "error",
                "mensagem": "Nome obrigatório"
            }), 400

        return jsonify({
            "status": "success",
            "mensagem": "Paciente recebido",
            "paciente": {
                "nome": nome,
                "telefone": telefone,
                "cpf": cpf,
                "pressao": pressao
            }
        }), 201


    return jsonify({
        "status": "success",
        "pacientes": []
    })
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def role_required(module):
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            from dv_saude import role_allows

            if not session.get("user_id"):
                return redirect(url_for("login", next=request.url))
            if not role_allows(module, session.get("user_perfil")):
                return "Acesso não autorizado para este perfil.", 403
            if (
                session.get("user_perfil") != "ADMIN"
                and not session.get("cnes_unidade")
            ):
                return "A conta não está vinculada a uma unidade CNES.", 403
            return view(*args, **kwargs)

        return wrapped

    return decorate


def _active_cnes_unidade():
    if session.get("user_perfil") == "ADMIN":
        return None
    return session.get("cnes_unidade")
def gerar_texto_termo(paciente: dict, tipo: str, procedimento: str, valor: str, usar_imagem: str, riscos: str) -> str:
    data_atual = date.today().strftime('%d/%m/%Y')
    nome = paciente.get('nome', 'Paciente')
    cpf = paciente.get('cpf', 'Não informado')
    texto_riscos = riscos or 'O paciente foi informado sobre os riscos gerais do procedimento e as possíveis complicações inerentes ao tratamento.'
    texto_imagem = 'O paciente autoriza o uso de imagens do rosto para fins de documentação clínica e melhoria dos processos internos.' if usar_imagem == 'sim' else 'O paciente não autoriza o uso de imagens do rosto.'

    return f"""TERMO: {tipo}\n\n""" + (
        f"Eu, {nome}, CPF {cpf}, declaro que fui informado(a) sobre o procedimento {procedimento or 'não especificado'}, o valor combinado de R$ {valor or '0,00'} e os riscos gerais associados.\n\n"
        f"{texto_riscos}\n\n"
        f"{texto_imagem}\n\n"
        f"Declaro estar ciente de que este termo serve como autorização e consentimento para o procedimento descrito.\n\n"
        f"Data: {data_atual}\n"
        f"Assinatura: ________________________________\n"
    )

# --- ESTRUTURA BASE HTML (Global e visível por todas as rotas) ---
def base_html() -> str:
    user_nome = escape(session.get('user_nome', 'Usuário'))
    user_perfil = escape(session.get('user_perfil', 'GERAL'))
    from dv_saude import role_allows

    nav_items = [
        ("Visão geral", "/", "overview", True),
        ("DV Prontuário", "/prontuario/", "clinical", role_allows("clinical", session.get("user_perfil"))),
        ("DV Odonto", "/odonto/", "clinical", role_allows("clinical", session.get("user_perfil"))),
        ("DV Farmácia SUS", "/farmacia-sus/", "pharmacy", role_allows("pharmacy", session.get("user_perfil"))),
        ("DV Gestão Pública", "/gestao-publica/", "admin", role_allows("admin", session.get("user_perfil"))),
        ("DV Integrações", "/integracoes/", "admin", role_allows("admin", session.get("user_perfil"))),
        ("Configurações", "/configuracoes/", "admin", role_allows("admin", session.get("user_perfil"))),
        ("Usuários", "/usuarios", "admin", role_allows("admin", session.get("user_perfil"))),
    ]
    def nav_is_active(path):
        if request.path == path or (path != "/" and request.path.startswith(path)):
            return True
        if path == "/prontuario/" and request.path == "/prontuario":
            return True
        if path == "/odonto/" and request.path in {
            "/pacientes",
            "/odontograma",
            "/agenda",
            "/documentos",
            "/receita",
            "/relatorio",
            "/calculadora",
        }:
            return True
        return False

    links = "".join(
        f'<a class="{"active" if nav_is_active(path) else ""}" href="{path}">{label}</a>'
        for label, path, _, allowed in nav_items
        if allowed
    )
    user_actions = (
        f'<form class="global-search" action="/busca/" method="get"><input aria-label="Busca global" name="q" minlength="2" maxlength="80" placeholder="Buscar" required><button aria-label="Buscar">⌕</button></form>'
        if session.get("user_id") and role_allows("admin", session.get("user_perfil"))
        else ""
    )
    auth_link = (
        f'<form method="post" action="/logout"><input type="hidden" name="logout_csrf" '
        f'value="{escape(_auth_csrf_token())}"><button class="logout-link" type="submit">'
        f'Sair</button></form>'
        if session.get("user_id")
        else '<a href="/login">Entrar</a>'
    )
    
    return f"""
    <!DOCTYPE html>
    <html lang="pt-br">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{{{{ titulo }}}}</title>
        <style>
            * {{ box-sizing: border-box; }}
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; background: #f8fafc; color: #0f172a; }}
            .navbar {{ background: #ffffff; padding: 12px 20px; display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 12px; color: #0f172a; border-bottom: 1px solid #1e3a8a; }}
            .brand {{ color: #0f172a; font-weight: 700; font-size: 18px; white-space: nowrap; }}
            .nav-links {{ display: flex; align-items: center; flex-wrap: wrap; gap: 4px; }}
            .nav-links a {{ color: #334155; text-decoration: none; padding: 8px 10px; border-radius: 7px; font-weight: 500; font-size: 13px; }}
            .nav-links a:hover, .nav-links a.active {{ color: #1e3a8a; background: #eff6ff; }}
            .nav-links .logout-link {{ color: #dc2626; }}
            .nav-links form {{ margin: 0; }}
            .nav-links form button {{ border: 0; background: transparent; cursor: pointer; font: inherit; }}
            .global-search {{ display: flex; gap: 4px; }}
            .global-search input {{ width: 118px; padding: 7px; border: 1px solid #cbd5e1; border-radius: 6px; }}
            .global-search button {{ border: 0; border-radius: 6px; background: #1e3a8a; color: white; padding: 5px 10px; }}
            .container {{ max-width: 1200px; margin: 20px auto; padding: 0 15px; }}
            .badge-perfil {{ background: #dbeafe; color: #1d4ed8; padding: 3px 8px; border-radius: 12px; font-size: 11px; text-transform: uppercase; margin-left: 6px; }}
            .hero {{ background: linear-gradient(120deg,#eff6ff,#fff); border: 1px solid #dbeafe; border-radius: 14px; padding: 28px; margin: 18px 0 22px; }}
            .hero h1 {{ margin: 4px 0 8px; color: #0f172a; font-size: clamp(25px,4vw,36px); }}
            .hero p {{ color: #64748b; margin: 0; }}
            .eyebrow {{ color: #2563eb !important; font-size: 12px; font-weight: 700; letter-spacing: .1em; }}
            .cards {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(min(100%,230px),1fr)); gap: 16px; }}
            .module-card {{ display: block; color: inherit; text-decoration: none; background: #fff; border: 1px solid #dbe3ef; border-radius: 12px; padding: 20px; box-shadow: 0 4px 12px #0f172a0a; transition: transform .18s,border-color .18s; }}
            a.module-card:hover {{ transform: translateY(-2px); border-color: #2563eb; }}
            .module-card h2 {{ color: #1e3a8a; font-size: 18px; margin: 9px 0; }}
            .module-card p,.module-card small {{ color: #64748b; }}
            .module-card p {{ margin: 0; line-height: 1.5; font-size: 14px; }}
            .icon {{ font-size: 24px; }}
            .stats {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(150px,1fr)); gap: 12px; margin: 18px 0; }}
            .stat,.panel {{ padding: 17px; background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; }}
            .stat span {{ display: block; color: #64748b; font-size: 13px; }}
            .stat strong {{ display: block; color: #1e3a8a; font-size: 25px; margin-top: 6px; }}
            .subnav {{ display: flex; flex-wrap: wrap; gap: 6px; padding: 7px; border: 1px solid #e2e8f0; border-radius: 9px; background: white; }}
            .subnav-link {{ color: #334155; padding: 8px 10px; border-radius: 6px; font-size: 13px; text-decoration: none; }}
            .subnav-link:hover,.subnav-link.selected {{ background: #eff6ff; color: #1e3a8a; }}
            .form-grid {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(min(100%,210px),1fr)); gap: 12px; margin: 14px 0; }}
            .form-grid label {{ color: #475569; font-size: 13px; font-weight: 600; }}
            .form-grid input,.form-grid select,.search-form input {{ display: block; width: 100%; margin-top: 5px; padding: 10px; border: 1px solid #cbd5e1; border-radius: 7px; background: white; color: #0f172a; }}
            .button {{ align-self: end; width: fit-content; border: 0; border-radius: 7px; padding: 11px 15px; color: white; background: #1e3a8a; font-weight: 700; cursor: pointer; }}
            .table-wrap {{ max-width: 100%; overflow-x: auto; background: white; border: 1px solid #e2e8f0; border-radius: 9px; margin: 15px 0; }}
            table {{ border-collapse: collapse; width: 100%; min-width: 580px; text-align: left; }}
            th,td {{ padding: 10px 12px; border-bottom: 1px solid #e2e8f0; }}
            th {{ background: #f1f5f9; color: #475569; font-size: 13px; }}
            .empty {{ color: #94a3b8; text-align: center; padding: 22px; }}
            .notice {{ padding: 12px 14px; border: 1px solid #bfdbfe; border-radius: 8px; color: #1e40af; background: #eff6ff; line-height: 1.5; }}
            .error {{ color: #b91c1c; background: #fef2f2; padding: 10px; border-radius: 7px; }}
            .section-title {{ color: #1e3a8a; margin-top: 26px; }}
            .search-form {{ display: flex; flex-wrap: wrap; align-items: end; gap: 10px; }}
            .search-form label {{ flex: 1 1 240px; }}
            .results {{ background: white; border: 1px solid #e2e8f0; border-radius: 9px; padding: 18px 38px; line-height: 2.2; }}
            @media(max-width:760px) {{ .navbar {{ align-items: flex-start; }} .nav-links {{ width: 100%; gap: 2px; }} .nav-links a {{ padding: 8px 7px; }} .global-search {{ margin: 5px; }} .container {{ padding: 0 12px; }} .hero {{ padding: 20px; }} }}
            @media(max-width:420px) {{ .nav-links a {{ font-size: 12px; }} .badge-perfil {{ display: none; }} }}
        </style>
    </head>
    <body>
        <div class="navbar">
            <div class="brand">🏥 DV SAÚDE <span class="badge-perfil">{user_perfil}</span></div>
            <nav class="nav-links" aria-label="Navegação principal">
                {links}
                {user_actions}
                <span style="font-size: 13px;">👤 {user_nome}</span>
                {auth_link}
            </nav>
        </div>
        <div class="container">
            {{{{ conteudo | safe }}}}
        </div>
    </body>
    </html>
    """
def calcular_imc(peso, altura):
    try:
        # Garante a conversão para float
        p = float(peso)
        a = float(altura)

        # Se o usuário digitar a altura em centímetros (ex: 175 em vez de 1.75)
        if a > 3.0:
            a = a / 100.0

        if a <= 0 or p <= 0:
            return 0, "Valores devem ser maiores que zero"

        imc = p / (a ** 2)

        if imc < 18.5:
            classe = "Abaixo do peso"
        elif 18.5 <= imc < 25:
            classe = "Peso normal"
        elif 25 <= imc < 30:
            classe = "Sobrepeso"
        elif 30 <= imc < 35:
            classe = "Obesidade Grau I"
        elif 35 <= imc < 40:
            classe = "Obesidade Grau II"
        else:
            classe = "Obesidade Grau III (Mórbida)"

        return round(imc, 2), classe

    except (ZeroDivisionError, ValueError, TypeError):
        return 0, "Dados de peso ou altura inválidos"

def verificar_prioridade(classe):
    if classe in ["Obesidade Grau II", "Obesidade Grau III (Mórbida)", "Abaixo do peso"]:
        return '<span style="color: #dc2626; font-weight: bold;">ALTA (Requer Atenção)</span>'
    elif classe in ["Sobrepeso", "Obesidade Grau I"]:
        return '<span style="color: #d97706; font-weight: bold;">MÉDIA (Acompanhar)</span>'
    return '<span style="color: #16a34a; font-weight: bold;">BAIXA (Normal)</span>'

@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    csrf_token = _auth_csrf_token()
    if request.method == "POST":
        submitted_token = request.form.get("csrf_token", "")
        if not _validate_auth_csrf(submitted_token):
            return "Token de segurança inválido. Atualize a página e tente novamente.", 400
        username = request.form.get("usuario", "").strip()
        password = request.form.get("senha", "")
        user = database.autenticar_usuario(username, password) if username else None
        if user:
            session.clear()
            session["user_id"] = user["id"]
            session["user_nome"] = user["nome"]
            session["user_perfil"] = user["perfil"]
            session["cnes_unidade"] = user.get("cnes_unidade") or ""
            target = request.args.get("next", "")
            parsed_target = urlsplit(target)
            if (
                parsed_target.scheme == ""
                and parsed_target.netloc == ""
                and parsed_target.path.startswith("/")
                and "\\" not in target
            ):
                return redirect(target)
            return redirect(url_for("index"))
        error = "Usuário ou senha incorretos."
    content = f"""
      <section class="panel" style="max-width:420px;margin:48px auto">
        <h1 style="color:#1e3a8a">Entrar no DV Saúde</h1>
        {f'<p class="error">{escape(error)}</p>' if error else ''}
        <form method="post" class="form-grid" style="grid-template-columns:1fr">
          <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
          <label>Usuário<input name="usuario" autocomplete="username" required></label>
          <label>Senha<input name="senha" type="password" autocomplete="current-password" required></label>
          <button class="button" type="submit">Entrar</button>
        </form>
      </section>
    """
    return render_template_string(
        base_html(), titulo="Entrar — DV Saúde", conteudo=Markup(content)
    )


@app.route("/logout", methods=["POST"])
def logout():
    if not _validate_auth_csrf(request.form.get("logout_csrf", "")):
        return "Token de segurança inválido. Atualize a página e tente novamente.", 400
    session.clear()
    return redirect("/")


@app.route("/usuarios", methods=["GET", "POST"])
@role_required("admin")
def usuarios_page():
    message = ""
    if request.method == "POST":
        if not _validate_auth_csrf(request.form.get("csrf_token", "")):
            return "Token de segurança inválido. Atualize a página e tente novamente.", 400
        try:
            created = database.cadastrar_usuario(
                request.form.get("nome", ""),
                request.form.get("usuario", ""),
                request.form.get("senha", ""),
                request.form.get("perfil", ""),
                request.form.get("registro_profissional", ""),
                request.form.get("cnes_unidade", ""),
            )
        except ValueError as error:
            message = str(error)
        else:
            if created:
                return redirect(url_for("usuarios_page"))
            message = "Esse nome de usuário já está cadastrado."

    users = database.listar_usuarios()
    units = database.listar_unidades()
    unit_names = {unit["cnes"]: unit["nome"] for unit in units}
    rows = "".join(
        f"<tr><td>{escape(user['nome'])}</td><td>{escape(user['usuario'])}</td>"
        f"<td>{escape(user['perfil'])}</td><td>{escape(user['registro_profissional'] or '')}</td>"
        f"<td>{escape(unit_names.get(user.get('cnes_unidade'), 'Acesso global'))}</td></tr>"
        for user in users
    )
    profile_options = "".join(
        f'<option value="{profile}">{profile}</option>'
        for profile in sorted(database.ALLOWED_USER_PROFILES)
    )
    unit_options = '<option value="">Sem unidade (somente ADMIN)</option>' + "".join(
        f'<option value="{escape(unit["cnes"])}">{escape(unit["nome"])} — CNES {escape(unit["cnes"])}</option>'
        for unit in units
    )
    content = f"""
      <section class="hero"><p class="eyebrow">ADMINISTRAÇÃO</p>
      <h1>Usuários e permissões</h1>
      <p>Somente administradores podem criar contas. Senhas exigem pelo menos 12 caracteres.</p></section>
      <form class="panel form-grid" method="post">
        <input type="hidden" name="csrf_token" value="{escape(_auth_csrf_token())}">
        <label>Nome<input name="nome" maxlength="160" autocomplete="name" required></label>
        <label>Usuário<input name="usuario" maxlength="80" autocomplete="username" required></label>
        <label>Senha<input name="senha" type="password" minlength="12" autocomplete="new-password" required></label>
        <label>Perfil<select name="perfil" required>{profile_options}</select></label>
        <label>Unidade de saúde<select name="cnes_unidade">{unit_options}</select></label>
        <label>Registro profissional<input name="registro_profissional" maxlength="80"></label>
        <button class="button" type="submit">Cadastrar usuário</button>
      </form>
      {f'<p class="error">{escape(message)}</p>' if message else ''}
      <div class="table-wrap"><table><thead><tr><th>Nome</th><th>Usuário</th>
      <th>Perfil</th><th>Registro</th><th>Unidade</th></tr></thead><tbody>{rows}</tbody></table></div>
    """
    return render_template_string(
        base_html(), titulo="Usuários — DV Saúde", conteudo=Markup(content)
    )
@app.route('/prontuario', methods=['GET', 'POST'])
@role_required("clinical")
def prontuario_page():
    paciente_id = request.args.get('paciente_id') or request.form.get('paciente_id')
    
    if request.method == 'POST':
        subjetivo = request.form.get('subjetivo')
        objetivo = request.form.get('objetivo')
        avaliacao = request.form.get('avaliacao')
        plano = request.form.get('plano')

        if paciente_id and (subjetivo or objetivo or avaliacao or plano):
            database.salvar_soap(paciente_id, subjetivo, objetivo, avaliacao, plano, cnes_unidade=_active_cnes_unidade())
            return redirect(url_for('prontuario_page', paciente_id=paciente_id))

    pacientes = database.list_pacientes(_active_cnes_unidade())
    paciente_atual = next((p for p in pacientes if str(p.get("id")) == str(paciente_id)), None) if paciente_id else None
    
    historico_soap = database.listar_soap_paciente(paciente_id, _active_cnes_unidade()) if paciente_id else []

    # Opções do select de pacientes
    opt_pacientes = "".join([
        f'<option value="{p["id"]}" {"selected" if str(p["id"]) == str(paciente_id) else ""}>{p["nome"]} (CNS: {p.get("cns", "N/I")})</option>' 
        for p in pacientes
    ])

    cards_historico = ""
    for reg in historico_soap:
        # Trata o formato sqlite Row ou Tupla
        data_f = reg[2] if isinstance(reg, tuple) else reg["data_atendimento"]
        sub = reg[3] if isinstance(reg, tuple) else reg["subjetivo"]
        obj = reg[4] if isinstance(reg, tuple) else reg["objetivo"]
        ava = reg[5] if isinstance(reg, tuple) else reg["avaliacao"]
        pla = reg[6] if isinstance(reg, tuple) else reg["plano"]

        cards_historico += f"""
        <div style="background: #f8fafc; border-left: 4px solid #1e3a8a; border-radius: 6px; padding: 15px; margin-bottom: 15px; border-top: 1px solid #e2e8f0; border-right: 1px solid #e2e8f0; border-bottom: 1px solid #e2e8f0;">
            <div style="color: #64748b; font-size: 12px; font-weight: bold; margin-bottom: 8px;">
                📅 Atendimento em: {data_f}
            </div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; font-size: 13px;">
                <div><b style="color: #1e3a8a;">S (Subjetivo):</b> <p style="margin: 2px 0; color: #334155;">{sub or 'N/I'}</p></div>
                <div><b style="color: #1e3a8a;">O (Objetivo):</b> <p style="margin: 2px 0; color: #334155;">{obj or 'N/I'}</p></div>
                <div><b style="color: #1e3a8a;">A (Avaliação):</b> <p style="margin: 2px 0; color: #334155;">{ava or 'N/I'}</p></div>
                <div><b style="color: #1e3a8a;">P (Plano / Conduta):</b> <p style="margin: 2px 0; color: #334155;">{pla or 'N/I'}</p></div>
            </div>
        </div>
        """

    html_prontuario = f"""
    <div style="background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
        <h2 style="color: #1e3a8a; margin-top: 0; border-bottom: 2px solid #f1f5f9; padding-bottom: 10px;">
            🏥 Prontuário Eletrônico SOAP — e-SUS APS
        </h2>

        <form method="GET" style="margin-bottom: 20px; background: #f1f5f9; padding: 12px; border-radius: 6px;">
            <label><b>Selecione o Paciente para Prontuário:</b></label>
            <div style="display: flex; gap: 10px; margin-top: 5px;">
                <select name="paciente_id" onchange="this.form.submit()" style="flex: 1; padding: 8px; border-radius: 4px; border: 1px solid #cbd5e1;">
                    <option value="">-- Escolha um Paciente --</option>
                    {opt_pacientes}
                </select>
            </div>
        </form>

        {f'''
        <div style="background: #eff6ff; padding: 12px; border-radius: 6px; margin-bottom: 20px; border: 1px solid #bfdbfe;">
            <h3 style="margin: 0; color: #1e3a8a;">👤 Paciente: {paciente_atual["nome"]}</h3>
            <small style="color: #475569;">CNS: {paciente_atual.get("cns", "N/I")} | Telefone: {paciente_atual.get("telefone", "N/I")}</small>
        </div>

        <form method="POST" style="background: #fafafa; padding: 15px; border-radius: 8px; border: 1px solid #e2e8f0; margin-bottom: 25px;">
            <h4 style="margin-top: 0; color: #0f172a;">Nova Evolução Clínica (SOAP)</h4>
            <input type="hidden" name="paciente_id" value="{paciente_id}">
            
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
                <div>
                    <label style="font-weight: bold; color: #1e3a8a;">S - Subjetivo (Anamnese/Queixa):</label>
                    <textarea name="subjetivo" rows="3" placeholder="Ex: Paciente relata dor de dente no molar inferior..." style="width:100%; padding:8px; border-radius:4px; border:1px solid #cbd5e1; box-sizing: border-box;"></textarea>
                </div>
                <div>
                    <label style="font-weight: bold; color: #1e3a8a;">O - Objetivo (Exame Bucal/Achados):</label>
                    <textarea name="objetivo" rows="3" placeholder="Ex: Cavidade cariosa no elemento 36, sem edema..." style="width:100%; padding:8px; border-radius:4px; border:1px solid #cbd5e1; box-sizing: border-box;"></textarea>
                </div>
                <div>
                    <label style="font-weight: bold; color: #1e3a8a;">A - Avaliação (Diagnóstico/CID):</label>
                    <textarea name="avaliacao" rows="3" placeholder="Ex: Cárie dentária em dentina (K02.1)..." style="width:100%; padding:8px; border-radius:4px; border:1px solid #cbd5e1; box-sizing: border-box;"></textarea>
                </div>
                <div>
                    <label style="font-weight: bold; color: #1e3a8a;">P - Plano (Procedimento SIGTAP / Conduta):</label>
                    <textarea name="plano" rows="3" placeholder="Ex: Realizada restauração em resina e agendado retorno..." style="width:100%; padding:8px; border-radius:4px; border:1px solid #cbd5e1; box-sizing: border-box;"></textarea>
                </div>
            </div>

            <button type="submit" style="margin-top: 15px; background: #1e3a8a; color: white; border: none; padding: 10px 20px; border-radius: 6px; font-weight: bold; cursor: pointer; width: 100%;">
                💾 Salvar Evolução SOAP
            </button>
        </form>

        <h3 style="color: #1e3a8a;">Historico de Evoluções Registradas</h3>
        {cards_historico if cards_historico else '<p style="color: #94a3b8; font-style: italic;">Nenhuma evolução SOAP cadastrada para este paciente.</p>'}
        ''' if paciente_atual else '<p style="color: #64748b; text-align: center; padding: 20px;">Selecione um paciente acima para visualizar ou cadastrar evoluções do prontuário.</p>'}
    </div>
    """
    return render_template_string(base_html(), titulo="Prontuário SOAP — DV Odonto", conteudo=html_prontuario)
# --- MENU PRINCIPAL ---
# --- MENU PRINCIPAL ---
# -------------------------------------------------------------------
# ROTA DE RECEITUÁRIO E PRESCRIÇÃO ODONTOLÓGICA
# -------------------------------------------------------------------
@app.route('/receita', methods=['GET', 'POST'])
@role_required("clinical")
def receita_page():
    if request.method == 'POST':
        paciente_nome = request.form.get('paciente_nome', 'Paciente')
        medicamentos = request.form.get('medicamentos', '')
        orientacoes = request.form.get('orientacoes', '')
        
        pdf_buffer = gerador_pdf.gerar_receita_pdf(paciente_nome, medicamentos, orientacoes)
        
        return send_file(
            pdf_buffer,
            as_attachment=True,
            download_name=f"receita_{paciente_nome.replace(' ', '_')}.pdf",
            mimetype='application/pdf'
        )

    pacientes = database.list_pacientes(_active_cnes_unidade())
    opt_pacientes = "".join([f'<option value="{p["nome"]}">{p["nome"]}</option>' for p in pacientes])

    html_receita = f"""
    <div style="background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
        <h2 style="color: #1e3a8a; margin-top: 0; border-bottom: 2px solid #f1f5f9; padding-bottom: 10px;">
            💊 Emissão de Receituário Odontológico
        </h2>

        <form method="POST" style="background: #f8fafc; padding: 20px; border-radius: 8px; border: 1px solid #e2e8f0;">
            <div style="margin-bottom: 15px;">
                <label><b>Nome do Paciente*:</b></label>
                <select name="paciente_nome" required style="width:100%; padding:10px; border-radius:4px; border:1px solid #cbd5e1;">
                    <option value="">Selecione o Paciente...</option>
                    {opt_pacientes}
                </select>
            </div>

            <div style="margin-bottom: 15px;">
                <label><b>Prescrição Rápida (Modelos):</b></label><br>
                <button type="button" onclick="insertMed('Amoxicilina 500mg --- Tomar 1 cápsula de 8 em 8 horas por 7 dias.')" style="background:#e0f2fe; color:#0369a1; border:none; padding:6px 10px; border-radius:4px; cursor:pointer; font-size:12px; margin-right:5px; margin-top:5px;">💊 Amoxicilina 500mg</button>
                <button type="button" onclick="insertMed('Ibuprofeno 600mg --- Tomar 1 comprimido de 8 em 8 horas se houver dor.')" style="background:#e0f2fe; color:#0369a1; border:none; padding:6px 10px; border-radius:4px; cursor:pointer; font-size:12px; margin-right:5px; margin-top:5px;">💊 Ibuprofeno 600mg</button>
                <button type="button" onclick="insertMed('Dipirona 500mg --- Tomar 1 comprimido de 6 em 6 horas se houver dor.')" style="background:#e0f2fe; color:#0369a1; border:none; padding:6px 10px; border-radius:4px; cursor:pointer; font-size:12px; margin-top:5px;">💊 Dipirona 500mg</button>
            </div>

            <div style="margin-bottom: 15px;">
                <label><b>Medicamentos e Posologia*:</b></label>
                <textarea id="medicamentos" name="medicamentos" rows="6" required placeholder="Digite a prescrição..." style="width:100%; padding:10px; border-radius:4px; border:1px solid #cbd5e1; box-sizing:border-box;"></textarea>
            </div>

            <div style="margin-bottom: 20px;">
                <label><b>Orientações Adicionais / Recomendações:</b></label>
                <textarea name="orientacoes" rows="3" placeholder="Ex: Evitar alimentos quentes nas primeiras 24 horas." style="width:100%; padding:10px; border-radius:4px; border:1px solid #cbd5e1; box-sizing:border-box;"></textarea>
            </div>

            <button type="submit" style="background: #16a34a; color: white; border: none; padding: 12px; border-radius: 6px; font-weight: bold; cursor: pointer; width: 100%; font-size: 15px;">
                📄 Gerar e Baixar Receita em PDF
            </button>
        </form>
    </div>

    <script>
    function insertMed(texto) {{
        let campo = document.getElementById('medicamentos');
        if (campo.value) {{
            campo.value += '\\n' + texto;
        }} else {{
            campo.value = texto;
        }}
    }}
    </script>
    """
    return render_template_string(base_html(), titulo="Receituário — DV Odonto", conteudo=html_receita)
# --- PÁGINA INICIAL ---
# -------------------------------------------------------------------
# ===================================================================
# ROTA PRINCIPAL (INÍCIO - DASHBOARD)
# ===================================================================
@app.route("/")
def index() -> Any:
    from dv_saude import role_allows

    cards = [
        ("🩺", "DV Prontuário", "Histórico clínico e atendimentos multiprofissionais.", "/prontuario/", "clinical"),
        ("🦷", "DV Odonto", "Odontograma e gestão odontológica existentes.", "/odonto/", "clinical"),
        ("💊", "DV Farmácia SUS", "Medicamentos, lotes, estoque e dispensações.", "/farmacia-sus/", "pharmacy"),
        ("🏛️", "DV Gestão Pública", "Unidades, profissionais e indicadores disponíveis.", "/gestao-publica/", "admin"),
        ("🔗", "DV Integrações", "Acompanhamento do estado real dos conectores.", "/integracoes/", "admin"),
    ]
    card_html = "".join(
        (
            f'<a class="module-card" href="{path}"><span class="icon">{icon}</span>'
            f"<h2>{title}</h2><p>{description}</p></a>"
            if role_allows(permission, session.get("user_perfil"))
            else f'<article class="module-card" aria-disabled="true"><span class="icon">{icon}</span>'
            f"<h2>{title}</h2><p>{description}</p><small>Entre com um perfil autorizado para acessar.</small></article>"
        )
        for icon, title, description, path, permission in cards
    )
    stats_html = ""
    if session.get("user_id") and role_allows("clinical", session.get("user_perfil")):
        conn = database.conectar()
        try:
            cnes_unidade = _active_cnes_unidade()
            if cnes_unidade is None:
                patients = conn.execute("SELECT COUNT(*) FROM pacientes").fetchone()[0]
                appointments = conn.execute("SELECT COUNT(*) FROM agenda").fetchone()[0]
            else:
                patients = conn.execute(
                    "SELECT COUNT(*) FROM pacientes WHERE cnes_unidade=?",
                    (cnes_unidade,),
                ).fetchone()[0]
                appointments = conn.execute(
                    """
                    SELECT COUNT(*) FROM agenda a
                    JOIN pacientes p ON p.id=a.paciente_id
                    WHERE p.cnes_unidade=?
                    """,
                    (cnes_unidade,),
                ).fetchone()[0]
        finally:
            conn.close()
        stats_html = f"""
          <section class="stats" aria-label="Indicadores gerais">
            <article class="stat"><span>Pacientes cadastrados</span><strong>{patients}</strong></article>
            <article class="stat"><span>Consultas registradas</span><strong>{appointments}</strong></article>
          </section>
        """
    content = f"""
      <section class="hero"><p class="eyebrow">PLATAFORMA INTEGRADA DE SAÚDE</p>
      <h1>Bem-vindo ao DV Saúde</h1>
      <p>Acesse os módulos disponíveis para o seu perfil, preservando as ferramentas clínicas do DV Odonto.</p></section>
      {stats_html}
      <h2 class="section-title">Divisões da plataforma</h2>
      <section class="cards">{card_html}</section>
    """
    return render_template_string(
        base_html(), titulo="DV Saúde — Visão geral", conteudo=Markup(content)
    )

# --- CADASTRO E LISTAGEM DE PACIENTES ---
@app.route("/pacientes", methods=["GET", "POST"])
@role_required("clinical")
def pacientes_page() -> Any:
    if request.method == "POST":
        nome = request.form.get("nome") or ""
        telefone = request.form.get("telefone") or ""
        cpf = request.form.get("cpf") or ""
        altura = float(str(request.form.get("altura") or "0").replace(',', '.'))
        peso = float(str(request.form.get("peso") or "0").replace(',', '.'))
        pressao = request.form.get("pressao") or ""

        database.adicionar_paciente(nome, telefone, cpf, peso=peso, altura=altura, pressao=pressao, cnes_unidade=_active_cnes_unidade())
        return redirect("/pacientes")

    try:
        lista_pacientes = database.list_pacientes(_active_cnes_unidade())
        if not isinstance(lista_pacientes, list):
            lista_pacientes = []
    except Exception:
        lista_pacientes = []

    linhas_pacientes = ""
    for p in lista_pacientes:
        if isinstance(p, dict):
            p_id = p.get("id", "")
            p_nome = p.get("nome", "")
            p_tel = p.get("telefone", "-")
            p_cpf = p.get("cpf", "-")
            p_pressao = p.get("pressao", "-")
            p_peso = p.get("peso", "-")
            p_altura = p.get("altura", "-")
            p_imc = p.get("imc", "-")
            p_classificacao_imc = p.get("classificacao_imc", "-")
        else:
            try:
                p_id = p[0] if len(p) > 0 else ""
                p_nome = p[1] if len(p) > 1 else ""
                p_tel = p[2] if len(p) > 2 else "-"
                p_cpf = p[3] if len(p) > 3 else "-"
                p_pressao = p[7] if len(p) > 7 else "-"
                p_peso = p[8] if len(p) > 8 else "-"
                p_altura = p[9] if len(p) > 9 else "-"
                p_imc = p[10] if len(p) > 10 else "-"
                p_classificacao_imc = p[11] if len(p) > 11 else "-"
            except Exception:
                continue

        linhas_pacientes += "<tr>"
        linhas_pacientes += f'<td style="padding:12px; border-bottom:1px solid #e2e8f0; color:#475569;">{p_id}</td>'
        linhas_pacientes += f'<td style="padding:12px; border-bottom:1px solid #e2e8f0;"><b>{p_nome}</b></td>'
        linhas_pacientes += f'<td style="padding:12px; border-bottom:1px solid #e2e8f0; color:#475569;">{p_tel}</td>'
        linhas_pacientes += f'<td style="padding:12px; border-bottom:1px solid #e2e8f0; color:#475569;">{p_cpf}</td>'
        linhas_pacientes += f'<td style="padding:12px; border-bottom:1px solid #e2e8f0; color:#475569;">{p_pressao}</td>'
        linhas_pacientes += f'<td style="padding:12px; border-bottom:1px solid #e2e8f0; color:#475569;">{p_peso}</td>'
        linhas_pacientes += f'<td style="padding:12px; border-bottom:1px solid #e2e8f0; color:#475569;">{p_altura}</td>'
        linhas_pacientes += f'<td style="padding:12px; border-bottom:1px solid #e2e8f0; color:#475569;">{p_imc}</td>'
        linhas_pacientes += f'<td style="padding:12px; border-bottom:1px solid #e2e8f0; color:#475569;">{p_classificacao_imc}</td>'
        linhas_pacientes += f'''
            <td style="padding:12px; border-bottom:1px solid #e2e8f0; display:flex; gap:8px;">
                <a href="/paciente/{p_id}/editar" style="background:#2563eb; color:white; padding:6px 12px; text-decoration:none; border-radius:6px; font-size:13px; font-weight:bold;">Editar</a>
                <a href="/odontograma?paciente_id={p_id}" style="background:#22c55e; color:white; padding:6px 12px; text-decoration:none; border-radius:6px; font-size:13px; font-weight:bold;">Odontograma</a>
            </td>
        '''
        linhas_pacientes += "</tr>"

    html_pacientes = f"""
    <div style="background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
        <h2 style="color: #1e3a8a; margin-top: 0;">Pacientes Cadastrados</h2>
        <form method="POST" style="margin-bottom: 20px;">
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px;">
                <input type="text" name="nome" placeholder="Nome" style="padding: 10px; border: 1px solid #cbd5e1; border-radius: 8px;" required>
                <input type="text" name="telefone" placeholder="Telefone" style="padding: 10px; border: 1px solid #cbd5e1; border-radius: 8px;">
                <input type="text" name="cpf" placeholder="CPF" style="padding: 10px; border: 1px solid #cbd5e1; border-radius: 8px;">
                <input type="text" name="pressao" placeholder="Pressão (ex: 120/80)" style="padding: 10px; border: 1px solid #cbd5e1; border-radius: 8px;">
                <input type="text" name="peso" placeholder="Peso (kg)" style="padding: 10px; border: 1px solid #cbd5e1; border-radius: 8px;">
                <input type="text" name="altura" placeholder="Altura (m)" style="padding: 10px; border: 1px solid #cbd5e1; border-radius: 8px;">
            </div>
            <button type="submit" style="margin-top: 12px; background: #1e3a8a; color: white; border: none; padding: 12px 18px; border-radius: 8px; cursor: pointer;">Adicionar Paciente</button>
        </form>
        <table style="width: 100%; border-collapse: collapse; text-align: left;">
            <thead>
                <tr style="background-color: #f8fafc;">
                    <th style="padding: 12px; border-bottom: 2px solid #e2e8f0; color: #475569;">#</th>
                    <th style="padding: 12px; border-bottom: 2px solid #e2e8f0; color: #475569;">Nome</th>
                    <th style="padding: 12px; border-bottom: 2px solid #e2e8f0; color: #475569;">Telefone</th>
                    <th style="padding: 12px; border-bottom: 2px solid #e2e8f0; color: #475569;">CPF</th>
                    <th style="padding: 12px; border-bottom: 2px solid #e2e8f0; color: #475569;">Pressão</th>
                    <th style="padding: 12px; border-bottom: 2px solid #e2e8f0; color: #475569;">Peso</th>
                    <th style="padding: 12px; border-bottom: 2px solid #e2e8f0; color: #475569;">Altura</th>
                    <th style="padding: 12px; border-bottom: 2px solid #e2e8f0; color: #475569;">IMC</th>
                    <th style="padding: 12px; border-bottom: 2px solid #e2e8f0; color: #475569;">Classificação</th>
                    <th style="padding: 12px; border-bottom: 2px solid #e2e8f0; color: #475569;">Ações</th>
                </tr>
            </thead>
            <tbody>
                {linhas_pacientes}
            </tbody>
        </table>
    </div>
    """
    return render_template_string(base_html(), titulo="Pacientes — DV Odonto", conteudo=html_pacientes)

@app.route('/paciente/<int:paciente_id>/editar', methods=['GET', 'POST'])
@role_required("clinical")
def editar_paciente(paciente_id: int) -> Any:
    paciente = database.get_paciente(paciente_id, _active_cnes_unidade())
    if not paciente:
        return redirect('/pacientes')

    mensagem = ''
    if request.method == 'POST':
        nome = request.form.get('nome') or ''
        telefone = request.form.get('telefone') or ''
        cpf = request.form.get('cpf') or ''
        pressao = request.form.get('pressao') or ''
        peso = float(str(request.form.get('peso') or '0').replace(',', '.'))
        altura = float(str(request.form.get('altura') or '0').replace(',', '.'))

        database.atualizar_paciente(paciente_id, {
            'nome': nome,
            'telefone': telefone,
            'cpf': cpf,
            'pressao': pressao,
            'peso': peso,
            'altura': altura,
        }, cnes_unidade=_active_cnes_unidade())
        return redirect('/pacientes')

    paciente_nome = paciente.get('nome', '')
    paciente_telefone = paciente.get('telefone', '')
    paciente_cpf = paciente.get('cpf', '')
    paciente_pressao = paciente.get('pressao', '')
    paciente_peso = paciente.get('peso', '')
    paciente_altura = paciente.get('altura', '')

    html_editar = f"""
    <div style="background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); max-width: 640px; margin: 0 auto;">
        <h2 style="color: #1e3a8a; margin-top: 0;">Editar Paciente</h2>
        <p style="color: #475569; margin-bottom: 20px;">Atualize os dados de peso, altura e pressão para recalcular o IMC.</p>
        <form method="POST">
            <div style="display:grid; gap:14px;">
                <input type="text" name="nome" value="{paciente_nome}" placeholder="Nome" style="padding: 12px; border: 1px solid #cbd5e1; border-radius: 8px;">
                <input type="text" name="telefone" value="{paciente_telefone}" placeholder="Telefone" style="padding: 12px; border: 1px solid #cbd5e1; border-radius: 8px;">
                <input type="text" name="cpf" value="{paciente_cpf}" placeholder="CPF" style="padding: 12px; border: 1px solid #cbd5e1; border-radius: 8px;">
                <input type="text" name="pressao" value="{paciente_pressao}" placeholder="Pressão (ex: 120/80)" style="padding: 12px; border: 1px solid #cbd5e1; border-radius: 8px;">
                <input type="text" name="peso" value="{paciente_peso}" placeholder="Peso (kg)" style="padding: 12px; border: 1px solid #cbd5e1; border-radius: 8px;">
                <input type="text" name="altura" value="{paciente_altura}" placeholder="Altura (m)" style="padding: 12px; border: 1px solid #cbd5e1; border-radius: 8px;">
            </div>
            <div style="display:flex; gap: 12px; margin-top: 18px; flex-wrap: wrap;">
                <button type="submit" style="background: #1e3a8a; color: white; border: none; padding: 12px 18px; border-radius: 8px; cursor: pointer;">Salvar Atualizações</button>
                <a href="/pacientes" style="display:inline-flex; align-items:center; justify-content:center; padding: 12px 18px; border-radius: 8px; border: 1px solid #cbd5e1; color:#475569; text-decoration:none;">Voltar</a>
            </div>
        </form>
    </div>
    """
    return render_template_string(base_html(), titulo="Editar Paciente — DV Odonto", conteudo=html_editar)

# -------------------------------------------------------------------
# ROTA DE GERAÇÃO DE ATESTADO EM PDF
# -------------------------------------------------------------------
@app.route('/gerar_atestado', methods=['POST'])
@role_required("clinical")
def gerar_atestado():
    paciente_nome = request.form.get('paciente_nome', 'Paciente')
    dias = request.form.get('dias', '1')
    motivo = request.form.get('motivo', '')
    
    pdf_buffer = gerador_pdf.gerar_atestado_pdf(paciente_nome, dias, motivo)
    
    return send_file(
        pdf_buffer,
        as_attachment=True,
        download_name=f"atestado_{paciente_nome.replace(' ', '_')}.pdf",
        mimetype='application/pdf'
    )
@app.route('/documentos', methods=['GET', 'POST'])
@role_required("clinical")
def documentos_page() -> Any:
    pacientes = database.list_pacientes(_active_cnes_unidade())
    paciente_id = request.args.get('paciente_id') or request.form.get('paciente_id')
    paciente = database.get_paciente(int(paciente_id), _active_cnes_unidade()) if paciente_id and paciente_id.isdigit() else None
    mensagem = ''
    termo_text = ''

    if request.method == 'POST':
        if not paciente:
            mensagem = 'Selecione um paciente válido antes de enviar.'
        else:
            acao = request.form.get('acao', '')
            if acao == 'upload_documento':
                file = request.files.get('file')
                tipo = request.form.get('tipo', 'Documento')
                descricao = request.form.get('descricao', '')
                if file and file.filename != '':
                    original_name = secure_filename(file.filename) # type: ignore
                    stored_name = f"paciente_{paciente_id}_{uuid.uuid4().hex[:8]}_{original_name}"
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], stored_name)
                    file.save(filepath)
                    mime_type = file.mimetype or 'application/octet-stream'
                    database.adicionar_arquivo(int(paciente_id), tipo, original_name, stored_name, mime_type, descricao, cnes_unidade=_active_cnes_unidade()) # type: ignore
                    mensagem = f"Arquivo '{original_name}' carregado com sucesso."
                else:
                    mensagem = 'Selecione um arquivo para envio.'
            elif acao == 'gerar_termo':
                tipo = request.form.get('tipo_termo', 'Termo de Consentimento')
                procedimento = request.form.get('procedimento', '')
                valor = request.form.get('valor', '')
                usar_imagem = request.form.get('usar_imagem', 'nao')
                riscos = request.form.get('riscos', '')
                termo_text = gerar_texto_termo(paciente, tipo, procedimento, valor, usar_imagem, riscos)
                original_name = secure_filename(f"{tipo} - {procedimento or 'Procedimento'}.txt")
                stored_name = f"paciente_{paciente_id}_{uuid.uuid4().hex[:8]}_{original_name}"
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], stored_name)
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(termo_text)
                database.adicionar_arquivo(int(paciente_id), tipo, original_name, stored_name, 'text/plain', f"Termo automático gerado: {procedimento}", cnes_unidade=_active_cnes_unidade()) # type: ignore
                mensagem = f"Termo '{tipo}' gerado e salvo."  

    arquivos_html = ''
    if paciente:
        arquivos = database.listar_arquivos_paciente(int(paciente_id), _active_cnes_unidade()) # type: ignore
        if arquivos:
            for arq in arquivos:
                descricao_text = f" — {arq.get('descricao', '')}" if arq.get('descricao') else ''
                arquivos_html += f"<div style=\"display:flex; justify-content:space-between; align-items:center; padding:10px; background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; margin-bottom:8px;\">"
                arquivos_html += f"<div><strong>{arq.get('tipo')}</strong>: {arq.get('nome_original')}<br><span style='font-size:13px; color:#475569;'>{arq.get('criado_em', '')}{descricao_text}</span></div>"
                arquivos_html += f"<a href=\"/static/uploads/{arq.get('nome_stored')}\" target=\"_blank\" style=\"color:#1e3a8a; text-decoration:none; font-weight:bold; font-size:14px;\">Abrir</a></div>"
        else:
            arquivos_html = '<p style="color:#94a3b8; font-size:14px; font-style:italic;">Nenhum documento ou termo encontrado para este paciente.</p>'
    else:
        arquivos_html = '<p style="color:#94a3b8; font-size:14px; font-style:italic;">Selecione um paciente para ver documentos e gerar termos.</p>'

    opcoes_pacientes = ''.join([f'<option value="{p["id"]}" {"selected" if paciente and p["id"] == paciente.get("id") else ""}>{p["nome"]}</option>' for p in pacientes])
    paciente_id_value = paciente_id or ''
    tipo_termo_default = 'Termo de Consentimento'

    html_documentos = f"""
    <div style="background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
        <h2 style="color: #1e3a8a; margin-top: 0;">Documentos e Termos Automáticos</h2>
        <form method="GET" style="margin-bottom: 20px; display: grid; grid-template-columns: minmax(220px, 1fr) 140px; gap: 12px; align-items: end;">
            <label style="display:block;">
                <span style="display:block; margin-bottom: 6px; color:#475569; font-weight:500;">Paciente</span>
                <select name="paciente_id" style="width:100%; padding:10px; border:1px solid #cbd5e1; border-radius:8px;">
                    <option value="">Selecione um paciente</option>
                    {opcoes_pacientes}
                </select>
            </label>
            <button type="submit" style="background:#1e3a8a; color:white; border:none; border-radius:8px; padding:12px; cursor:pointer; font-weight:bold;">Abrir Paciente</button>
        </form>

        <div style="display:grid; gap:20px;">
            <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; padding:20px;">
                <h3 style="color:#1e3a8a; margin-top:0;">1. Anexar Documento</h3>
                <form action="/upload_exame" method="POST" enctype="multipart/form-data" style="display:grid; gap:12px;">
                    <input type="hidden" name="paciente_id" value="{paciente_id_value}">
                    <input type="hidden" name="next" value="/documentos?paciente_id={paciente_id_value}">
                    <input type="hidden" name="tipo" value="Documento">
                    <label style="display:block;">
                        <span style="color:#475569; font-weight:500;">Arquivo</span>
                        <input type="file" name="file" required style="width:100%; padding:10px; border:1px solid #cbd5e1; border-radius:8px; background:#ffffff;">
                    </label>
                    <label style="display:block;">
                        <span style="color:#475569; font-weight:500;">Descrição (opcional)</span>
                        <input type="text" name="descricao" placeholder="Ex: Exame panorâmico" style="width:100%; padding:10px; border:1px solid #cbd5e1; border-radius:8px;">
                    </label>
                    <button type="submit" style="background:#1e3a8a; color:white; border:none; border-radius:8px; padding:12px; cursor:pointer; font-weight:bold;">Enviar Documento</button>
                </form>
            </div>

            <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; padding:20px;">
                <h3 style="color:#1e3a8a; margin-top:0;">2. Gerar Termo Automático</h3>
                <form method="POST" style="display:grid; gap:12px;">
                    <input type="hidden" name="paciente_id" value="{paciente_id_value}">
                    <input type="hidden" name="acao" value="gerar_termo">
                    <label style="display:block;">
                        <span style="color:#475569; font-weight:500;">Tipo de Termo</span>
                        <select name="tipo_termo" style="width:100%; padding:10px; border:1px solid #cbd5e1; border-radius:8px;">
                            <option value="Termo de Consentimento">Termo de Consentimento</option>
                            <option value="Termo Esclarecido">Termo Esclarecido</option>
                        </select>
                    </label>
                    <label style="display:block;">
                        <span style="color:#475569; font-weight:500;">Procedimento</span>
                        <input type="text" name="procedimento" placeholder="Ex: Extração, Implante, Clareamento" style="width:100%; padding:10px; border:1px solid #cbd5e1; border-radius:8px;">
                    </label>
                    <label style="display:block;">
                        <span style="color:#475569; font-weight:500;">Valor combinado</span>
                        <input type="text" name="valor" placeholder="Ex: 450,00" style="width:100%; padding:10px; border:1px solid #cbd5e1; border-radius:8px;">
                    </label>
                    <label style="display:block;">
                        <span style="color:#475569; font-weight:500;">Riscos gerais (opcional)</span>
                        <textarea name="riscos" rows="3" style="width:100%; padding:10px; border:1px solid #cbd5e1; border-radius:8px;"></textarea>
                    </label>
                    <label style="display:block;">
                        <span style="color:#475569; font-weight:500;">Uso de imagens do rosto</span>
                        <select name="usar_imagem" style="width:100%; padding:10px; border:1px solid #cbd5e1; border-radius:8px;">
                            <option value="sim">Autorizado</option>
                            <option value="nao">Não autorizado</option>
                        </select>
                    </label>
                    <button type="submit" style="background:#1e3a8a; color:white; border:none; border-radius:8px; padding:12px; cursor:pointer; font-weight:bold;">Gerar e Salvar Termo</button>
                </form>
            </div>

            <div style="background:#ffffff; border:1px solid #cbd5e1; border-radius:8px; padding:20px;">
                <h3 style="color:#1e3a8a; margin-top:0;">3. Documentos do Paciente</h3>
                {arquivos_html}
            </div>
        </div>
        {f'<div style="margin-top:18px; padding:15px; border-radius:8px; background:#e0f2fe; color:#0c4a6e;">{mensagem}</div>' if mensagem else ''}
        {f'<pre style="white-space:pre-wrap; word-break:break-word; background:#f8fafc; border:1px solid #cbd5e1; border-radius:8px; padding:15px; margin-top:20px;">{termo_text}</pre>' if termo_text else ''}
    </div>
    """

    return render_template_string(base_html(), titulo="Documentos — DV Odonto", conteudo=html_documentos)


# --- ODONTOGRAMA INTERATIVO ---
@app.route("/odontograma", methods=["GET", "POST"])
@role_required("clinical")
def odontograma() -> Any:
    pacientes = database.list_pacientes(_active_cnes_unidade())
    paciente_id = request.args.get("paciente_id")
    
    if not paciente_id and pacientes:
        paciente_id = str(pacientes[0]["id"])
    elif not paciente_id:
        paciente_id = None

    if request.method == "POST":
        if not paciente_id:
            return redirect("/pacientes")

        dente = request.form.get("dente") or ""
        face = request.form.get("face") or ""
        condicao = request.form.get("condicao") or ""
        
        if condicao == "extraido":
            database.salvar_condicao_dente(paciente_id, dente, "geral", "extraido", cnes_unidade=_active_cnes_unidade())
        else:
            database.salvar_condicao_dente(paciente_id, dente, face, condicao, cnes_unidade=_active_cnes_unidade())
            
        return redirect(f"/odontograma?paciente_id={paciente_id}")

    try:
        historico_dentes = database.obter_odontograma_paciente(paciente_id, _active_cnes_unidade()) if paciente_id else {}
        if not isinstance(historico_dentes, dict):
            historico_dentes = {}
    except Exception:
        historico_dentes = {}

    sup_esq = [18, 17, 16, 15, 14, 13, 12, 11]
    sup_dir = [21, 22, 23, 24, 25, 26, 27, 28]
    inf_esq = [48, 47, 46, 45, 44, 43, 42, 41]
    inf_dir = [31, 32, 33, 34, 35, 36, 37, 38]
    infantil_sup = [55, 54, 53, 52, 51, 61, 62, 63, 64, 65]
    infantil_inf = [85, 84, 83, 82, 81, 71, 72, 73, 74, 75]

    def renderizar_bloco_dente(num: int) -> str:
        estado = historico_dentes.get(str(num)) or {}
        v_c = f"marcada-{estado.get('V')}" if estado.get('V') else ""
        o_c = f"marcada-{estado.get('O')}" if estado.get('O') else ""
        m_c = f"marcada-{estado.get('M')}" if estado.get('M') else ""
        d_c = f"marcada-{estado.get('D')}" if estado.get('D') else ""
        l_c = f"marcada-{estado.get('L')}" if estado.get('L') else ""
        
        is_extraido = estado.get('geral') == 'extraido'
        extraido_class = "dente-extraido" if is_extraido else ""
        
        # SVG com overlay visual para dente extraído/ausente
        overlay_extraido = """
            <line x1="0" y1="0" x2="40" y2="40" stroke="#ef4444" stroke-width="2" />
            <line x1="40" y1="0" x2="0" y2="40" stroke="#ef4444" stroke-width="2" />
        """ if is_extraido else ""

        return f"""
        <div class="bloco-dente {extraido_class}" style="display: inline-block; margin: 4px; text-align: center;">
            <div style="font-weight: bold; font-size: 12px; margin-bottom: 2px; color: #1e3a8a;">{num}</div>
            <svg class="dente-svg" viewBox="0 0 40 40" style="width: 40px; height: 40px; border: 1px solid #cbd5e1; background: #ffffff; position: relative;">
                <polygon class="face {v_c}" points="0,0 40,0 30,10 10,10" fill="#f1f5f9" stroke="#94a3b8" stroke-width="0.5" onclick="marcarFace('{num}', 'V')" style="cursor:pointer;"/>
                <polygon class="face {o_c}" points="10,10 30,10 30,30 10,30" fill="#f8fafc" stroke="#94a3b8" stroke-width="0.5" onclick="marcarFace('{num}', 'O')" style="cursor:pointer;"/>
                <polygon class="face {m_c}" points="0,0 10,10 10,30 0,40" fill="#f1f5f9" stroke="#94a3b8" stroke-width="0.5" onclick="marcarFace('{num}', 'M')" style="cursor:pointer;"/>
                <polygon class="face {d_c}" points="40,0 40,40 30,30 30,10" fill="#f1f5f9" stroke="#94a3b8" stroke-width="0.5" onclick="marcarFace('{num}', 'D')" style="cursor:pointer;"/>
                <polygon class="face {l_c}" points="10,30 30,30 40,40 0,40" fill="#f1f5f9" stroke="#94a3b8" stroke-width="0.5" onclick="marcarFace('{num}', 'L')" style="cursor:pointer;"/>
                {overlay_extraido}
            </svg>
        </div>
        """

    html_adulto_sup = "".join([renderizar_bloco_dente(d) for d in sup_esq]) + " <span style='margin:0 10px; font-weight:bold; color:#94a3b8;'>|</span> " + "".join([renderizar_bloco_dente(d) for d in sup_dir])
    html_infantil_sup = "".join([renderizar_bloco_dente(d) for d in infantil_sup])
    html_infantil_inf = "".join([renderizar_bloco_dente(d) for d in infantil_inf])
    html_adulto_inf = "".join([renderizar_bloco_dente(d) for d in inf_esq]) + " <span style='margin:0 10px; font-weight:bold; color:#94a3b8;'>|</span> " + "".join([renderizar_bloco_dente(d) for d in inf_dir])

    opcoes_filtro_pacientes = "".join([f'<option value="{p["id"]}" {"selected" if str(p["id"])==str(paciente_id) else ""}>{p["nome"]}</option>' for p in pacientes])
    paciente_id_value = paciente_id or ""

    arquivos_html = ""
    if paciente_id:
        try:
            todos_arquivos = os.listdir(app.config['UPLOAD_FOLDER'])
            arquivos_paciente = [f for f in todos_arquivos if f.startswith(f"paciente_{paciente_id}_")]
            if arquivos_paciente:
                for arq in arquivos_paciente:
                    nome_exibicao = arq.replace(f"paciente_{paciente_id}_", "")
                    url_arquivo = f"/static/uploads/{arq}"
                    arquivos_html += f'<div style="display:flex; justify-content:space-between; align-items:center; padding:10px; background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; margin-bottom:8px;"><span style="color:#334155; font-size:14px;">📄 {nome_exibicao}</span><a href="{url_arquivo}" target="_blank" style="color:#1e3a8a; text-decoration:none; font-weight:bold; font-size:14px;">Visualizar</a></div>'
            else:
                arquivos_html = '<p style="color:#94a3b8; font-size:14px; font-style:italic;">Nenhum exame ou laudo anexado para este paciente.</p>'
        except Exception:
            arquivos_html = '<p style="color:#94a3b8; font-size:14px; font-style:italic;">Diretório de anexos indisponível.</p>'
    else:
        arquivos_html = '<p style="color:#94a3b8; font-size:14px; font-style:italic;">Selecione um paciente para ver os anexos.</p>'

    bloco_upload = f"""
<div style="margin-top: 30px; background: #ffffff; padding: 20px; border-radius: 8px; border: 1px solid #e2e8f0;">
    <h3 style="color: #1e3a8a; margin-top: 0; margin-bottom: 15px; border-bottom: 1px solid #f1f5f9; padding-bottom: 8px;">📁 Exames, Laudos e Panorâmicas</h3>
    <form action="/upload_exame" method="POST" enctype="multipart/form-data" style="display: flex; gap: 10px; margin-bottom: 20px;">
        <input type="hidden" name="paciente_id" value="{paciente_id_value}">
        <input type="file" name="file" required style="flex-grow: 1; padding: 8px; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 14px; background: #f8fafc;">
        <button type="submit" style="background: #1e3a8a; color: white; padding: 8px 16px; border: none; border-radius: 6px; font-weight: bold; cursor: pointer; font-size: 14px;">Anexar Arquivo</button>
    </form>
    <div style="max-height: 200px; overflow-y: auto;">{arquivos_html}</div>
</div>
"""

    html_odontograma = f"""
    <style>
        .marcada-restaurar {{ fill: #ef4444 !important; }} /* Vermelho */
        .marcada-feito {{ fill: #22c55e !important; }}     /* Verde */
        .dente-extraido {{ opacity: 0.5; }}
    </style>

    <div style="display:flex; justify-content:space-between; align-items:center; border-bottom: 2px solid #e2e8f0; padding-bottom: 10px; margin-bottom:20px;">
        <h2 style="color: #1e3a8a; margin:0;">Odontograma Clínico</h2>
        <div>
            <label style="font-weight:bold; color:#475569; margin-right:8px;">Paciente:</label>
            <select onchange="location.href='/odontograma?paciente_id='+this.value" style="padding:8px; border-radius:6px; border:1px solid #cbd5e1; font-weight:500;">
                {opcoes_filtro_pacientes if opcoes_filtro_pacientes else '<option>Nenhum paciente cadastrado</option>'}
            </select>
        </div>
    </div>
    
    <div style="text-align: center; background: #f1f5f9; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
        <h4 style="margin: 0 0 10px 0; color: #475569;">Arcada Superior (Adulto)</h4>
        <div style="overflow-x:auto; white-space:nowrap;">{html_adulto_sup}</div>
    </div>

    <div style="text-align: center; background: #fafafa; padding: 15px; border-radius: 8px; margin-bottom: 20px; border: 1px dashed #cbd5e1;">
        <h4 style="margin: 0 0 10px 0; color: #475569;">Arcada Decídua (Infantil)</h4>
        <div style="overflow-x:auto; white-space:nowrap;">{html_infantil_sup}</div>
        <div style="margin-top: 10px; overflow-x:auto; white-space:nowrap;">{html_infantil_inf}</div>
    </div>

    <div style="text-align: center; background: #f1f5f9; padding: 15px; border-radius: 8px; margin-bottom: 25px;">
        <h4 style="margin: 0 0 10px 0; color: #475569;">Arcada Inferior (Adulto)</h4>
        <div style="overflow-x:auto; white-space:nowrap;">{html_adulto_inf}</div>
    </div>

    <div id="modalProcedimento" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.5); justify-content:center; align-items:center; z-index:9999;">
        <div style="background:white; padding:25px; border-radius:8px; width:320px; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.3);">
            <h3 id="modalTitulo" style="margin-top:0; color:#1e3a8a;">Modificar Condição</h3>
            <form method="POST">
                <input type="hidden" id="formDente" name="dente">
                <input type="hidden" id="formFace" name="face">
                
                <div style="margin: 15px 0;">
                    <label style="display:block; margin-bottom:6px; font-weight:500; color:#334155;">Condição Clínica:</label>
                    <select name="condicao" style="width:100%; padding:8px; border-radius:6px; border:1px solid #cbd5e1;">
                        <option value="restaurar">Necessita de Restauração (Vermelho)</option>
                        <option value="feito">Tratamento Concluído (Verde)</option>
                        <option value="extraido">Dente Ausente / Extraído</option>
                    </select>
                </div>
                
                <div style="display:flex; gap:10px; margin-top:25px;">
                    <button type="submit" style="flex:1; background:#1e3a8a; color:white; padding:10px; border:none; border-radius:6px; cursor:pointer; font-weight:bold;">Salvar</button>
                    <button type="button" onclick="fecharModal()" style="flex:1; background:#e2e8f0; color:#334155; padding:10px; border:none; border-radius:6px; cursor:pointer; font-weight:bold;">Fechar</button>
                </div>
            </form>
        </div>
    </div>

    {VIEWER_3D}

    {bloco_upload}

    <script>
    function marcarFace(dente, face) {{
        document.getElementById('formDente').value = dente;
        document.getElementById('formFace').value = face;
        document.getElementById('modalTitulo').innerText = "Dente " + dente + " — Face " + face;
        document.getElementById('modalProcedimento').style.display = 'flex';
    }}
    function fecharModal() {{
        document.getElementById('modalProcedimento').style.display = 'none';
    }}
    </script>
    """
    return render_template_string(base_html(), titulo="Odontograma — DV Odonto", conteudo=html_odontograma)
@app.route('/agenda', methods=['GET', 'POST'])
@role_required("clinical")
def agenda_page():
    # Processa o formulário quando o usuário cadastra uma nova consulta
    if request.method == 'POST':
        paciente_id = request.form.get('paciente_id')
        data_hora = request.form.get('data_hora')
        procedimento = request.form.get('procedimento')
        especialidade = request.form.get('especialidade', 'Clínica Geral')

        if paciente_id and data_hora:
            database.adicionar_agendamento(paciente_id, data_hora, especialidade, procedimento, cnes_unidade=_active_cnes_unidade())
            return redirect(url_for('agenda_page'))

    # Carrega agendamentos com tratamento de erros
    try:
        compromissos = database.obter_agenda(_active_cnes_unidade())
        if not isinstance(compromissos, list):
            compromissos = []
    except Exception:
        compromissos = []

    # Carrega pacientes com tratamento de erros
    try:
        pacientes = database.list_pacientes(_active_cnes_unidade())
        if not isinstance(pacientes, list):
            pacientes = []
    except Exception:
        pacientes = []

    linhas_tabela = ""
    for comp in compromissos:
        # Busca o objeto do paciente para pegar nome e telefone
        paciente_obj = next(
            (p for p in pacientes if str(p.get("id")) == str(comp.get("paciente_id"))),
            None
        )
        
        nome_paciente = paciente_obj["nome"] if paciente_obj else "Paciente Não Selecionado"
        telefone = paciente_obj.get("telefone", "") if paciente_obj else ""

        # Trata a separação de data e hora
        data_hora_raw = comp.get("data_hora", "") or ""
        dh_normalizado = data_hora_raw.replace("T", " ")
        
        if " " in dh_normalizado:
            data, hora = dh_normalizado.split(" ", 1)
        else:
            data, hora = dh_normalizado, "Não informada"

        procedimento = comp.get("procedimento") or comp.get("especialidade") or "Não informado"

        # Gera o link dinâmico para mensagem do WhatsApp
        link_wa = whatsapp.gerar_link_lembrete_paciente(
            nome_paciente=nome_paciente,
            telefone=telefone,
            data_hora=dh_normalizado,
            procedimento=procedimento
        )

        linhas_tabela += f"""
        <tr>
            <td style="padding: 12px; border-bottom: 1px solid #e2e8f0; color: #475569;"><b>{data or "Não informada"}</b></td>
            <td style="padding: 12px; border-bottom: 1px solid #e2e8f0; color: #475569;">{hora or "Não informada"}</td>
            <td style="padding: 12px; border-bottom: 1px solid #e2e8f0;">
                <b>{nome_paciente}</b><br>
                <small style="color: #64748b;">📞 {telefone if telefone else "Sem número"}</small>
            </td>
            <td style="padding: 12px; border-bottom: 1px solid #e2e8f0; color: #1e3a8a; font-weight: 500;">{procedimento}</td>
            <td style="padding: 12px; border-bottom: 1px solid #e2e8f0; text-align: center;">
                <a href="{link_wa}" target="_blank" 
                   style="background: #25D366; color: white; padding: 6px 12px; border-radius: 6px; text-decoration: none; font-size: 12px; font-weight: bold; display: inline-block;">
                   📲 Avisar no WhatsApp
                </a>
            </td>
        </tr>
        """

    # Opções dos selects
    opt_pacientes = "".join([f'<option value="{p["id"]}">{p["nome"]} (CNS: {p.get("cns", "N/I")})</option>' for p in pacientes])
    lista_sigtap = PROCEDIMENTOS_SIGTAP if 'PROCEDIMENTOS_SIGTAP' in globals() else []
    opt_sigtap = "".join([f'<option value="{p["codigo"]} - {p["nome"]}">{p["codigo"]} - {p["nome"]}</option>' for p in lista_sigtap])

    html_agenda = f"""
    <div style="background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
        
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; border-bottom: 2px solid #f1f5f9; padding-bottom: 12px;">
            <h2 style="color: #1e3a8a; margin: 0;">📅 Calendário e Agendamentos — e-SUS</h2>
            <button onclick="window.print()" style="background: #64748b; color: white; border: none; padding: 8px 14px; border-radius: 6px; font-weight: bold; cursor: pointer; font-size: 13px;">🖨️ Imprimir Agenda</button>
        </div>

        <form method="POST" style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 25px; background: #f8fafc; padding: 15px; border-radius: 8px; border: 1px solid #e2e8f0;">
            <div>
                <label><b>Paciente*:</b></label>
                <select name="paciente_id" required style="width:100%; padding:8px; border-radius:4px; border:1px solid #cbd5e1;">
                    <option value="">Selecione o Paciente...</option>
                    {opt_pacientes}
                </select>
            </div>
            <div>
                <label><b>Data e Hora*:</b></label>
                <input type="datetime-local" name="data_hora" required style="width:100%; padding:8px; border-radius:4px; border:1px solid #cbd5e1;">
            </div>
            <div style="grid-column: span 2;">
                <label><b>Procedimento SIGTAP / SUS:</b></label>
                <select name="procedimento" style="width:100%; padding:8px; border-radius:4px; border:1px solid #cbd5e1;">
                    {opt_sigtap if opt_sigtap else '<option value="Consulta Odontologica">Consulta Odontológica Programada</option>'}
                </select>
            </div>
            <div style="grid-column: span 2;">
                <button type="submit" style="background: #1e3a8a; color: white; border: none; padding: 10px; border-radius: 6px; font-weight: bold; cursor: pointer; width: 100%;">Confirmar Agendamento</button>
            </div>
        </form>

        <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 25px; margin-bottom: 15px;">
            <h3 style="color: #1e3a8a; margin: 0;">Consultas Marcadas</h3>
        </div>

        <div style="margin-bottom: 15px;">
            <input type="text" id="inputBusca" onkeyup="filtrarAgenda()" placeholder="🔍 Buscar paciente na agenda por nome..." 
                   style="width: 100%; padding: 10px; border-radius: 6px; border: 1px solid #cbd5e1; box-sizing: border-box;">
        </div>

        <table style="width: 100%; border-collapse: collapse; text-align: left;">
            <thead>
                <tr style="background-color: #f8fafc;">
                    <th style="padding: 12px; border-bottom: 2px solid #e2e8f0; color: #475569;">Data</th>
                    <th style="padding: 12px; border-bottom: 2px solid #e2e8f0; color: #475569;">Hora</th>
                    <th style="padding: 12px; border-bottom: 2px solid #e2e8f0; color: #475569;">Paciente</th>
                    <th style="padding: 12px; border-bottom: 2px solid #e2e8f0; color: #475569;">Procedimento</th>
                    <th style="padding: 12px; border-bottom: 2px solid #e2e8f0; color: #475569; text-align: center;">Ações</th>
                </tr>
            </thead>
            <tbody>
                {linhas_tabela if linhas_tabela else '<tr><td colspan="5" style="padding: 20px; text-align: center; color: #94a3b8; font-style: italic;">Nenhum agendamento encontrado.</td></tr>'}
            </tbody>
        </table>
    </div>

    <script>
    function filtrarAgenda() {{
        let input = document.getElementById('inputBusca').value.toLowerCase();
        let tabela = document.querySelector('table tbody');
        let linhas = tabela.getElementsByTagName('tr');

        for (let i = 0; i < linhas.length; i++) {{
            let colunaPaciente = linhas[i].getElementsByTagName('td')[2]; // Coluna 3: Paciente
            if (colunaPaciente) {{
                let texto = colunaPaciente.textContent || colunaPaciente.innerText;
                if (texto.toLowerCase().indexOf(input) > -1) {{
                    linhas[i].style.display = "";
                }} else {{
                    linhas[i].style.display = "none";
                }}
            }}
        }}
    }}
    </script>
    """
    return render_template_string(base_html(), titulo="Agenda — DV Odonto SUS", conteudo=html_agenda)

    # Opções do select de pacientes
    opt_pacientes = "".join([f'<option value="{p["id"]}">{p["nome"]} (CNS: {p.get("cns", "N/I")})</option>' for p in pacientes])
    
    # Opções do select SIGTAP (caso exista a lista no app.py ou importada)
    lista_sigtap = PROCEDIMENTOS_SIGTAP if 'PROCEDIMENTOS_SIGTAP' in globals() else []
    opt_sigtap = "".join([f'<option value="{p["codigo"]} - {p["nome"]}">{p["codigo"]} - {p["nome"]}</option>' for p in lista_sigtap])

    html_agenda = f"""
    <div style="background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
        
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; border-bottom: 2px solid #f1f5f9; padding-bottom: 12px;">
            <h2 style="color: #1e3a8a; margin: 0;">📅 Calendário e Agendamentos — e-SUS</h2>
            <button onclick="window.print()" style="background: #64748b; color: white; border: none; padding: 8px 14px; border-radius: 6px; font-weight: bold; cursor: pointer; font-size: 13px;">🖨️ Imprimir Agenda</button>
        </div>

        <form method="POST" style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 25px; background: #f8fafc; padding: 15px; border-radius: 8px; border: 1px solid #e2e8f0;">
            <div>
                <label><b>Paciente*:</b></label>
                <select name="paciente_id" required style="width:100%; padding:8px; border-radius:4px; border:1px solid #cbd5e1;">
                    <option value="">Selecione o Paciente...</option>
                    {opt_pacientes}
                </select>
            </div>
            <div>
                <label><b>Data e Hora*:</b></label>
                <input type="datetime-local" name="data_hora" required style="width:100%; padding:8px; border-radius:4px; border:1px solid #cbd5e1;">
            </div>
            <div style="grid-column: span 2;">
                <label><b>Procedimento SIGTAP / SUS:</b></label>
                <select name="procedimento" style="width:100%; padding:8px; border-radius:4px; border:1px solid #cbd5e1;">
                    {opt_sigtap if opt_sigtap else '<option value="Consulta Odontologica">Consulta Odontológica Programada</option>'}
                </select>
            </div>
            <div style="grid-column: span 2;">
                <button type="submit" style="background: #1e3a8a; color: white; border: none; padding: 10px; border-radius: 6px; font-weight: bold; cursor: pointer; width: 100%;">Confirmar Agendamento</button>
            </div>
        </form>

        <h3 style="color: #1e3a8a; margin-top: 20px;">Consultas Marcadas</h3>
        <table style="width: 100%; border-collapse: collapse; text-align: left;">
            <thead>
                <tr style="background-color: #f8fafc;">
                    <th style="padding: 12px; border-bottom: 2px solid #e2e8f0; color: #475569;">Data</th>
                    <th style="padding: 12px; border-bottom: 2px solid #e2e8f0; color: #475569;">Hora</th>
                    <th style="padding: 12px; border-bottom: 2px solid #e2e8f0; color: #475569;">Paciente</th>
                    <th style="padding: 12px; border-bottom: 2px solid #e2e8f0; color: #475569;">Procedimento</th>
                    <th style="padding: 12px; border-bottom: 2px solid #e2e8f0; color: #475569; text-align: center;">Ações</th>
                </tr>
            </thead>
            <tbody>
                {linhas_tabela if linhas_tabela else '<tr><td colspan="5" style="padding: 20px; text-align: center; color: #94a3b8; font-style: italic;">Nenhum agendamento encontrado.</td></tr>'}
            </tbody>
        </table>
    </div>
    """
    return render_template_string(base_html(), titulo="Agenda — DV Odonto SUS", conteudo=html_agenda)

    return render_template_string(base_html(), titulo="Agenda — DV Odonto", conteudo=html_agenda)
@app.route('/calculadora', methods=['GET', 'POST'])
@role_required("clinical")
def calculadora_page():
    resultado = None
    if request.method == 'POST':
        try:
            peso = request.form.get('peso')
            altura = request.form.get('altura')
            if peso and altura:
                from app import calcular_imc
                resultado = calcular_imc(peso, altura)
        except Exception:
            resultado = (0, "Erro ao processar dados")

    html_calc = f"""
    <div style="background: white; padding: 25px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); max-width: 500px; margin: 0 auto;">
        <h2 style="color: #1e3a8a; margin-top: 0;">🧮 Calculadora Clínica (IMC)</h2>
        <form method="POST" style="display: flex; flex-direction: column; gap: 15px;">
            <div>
                <label style="display: block; font-weight: bold; color: #475569; margin-bottom: 5px;">Peso (kg):</label>
                <input type="number" step="0.1" name="peso" required style="width: 100%; padding: 10px; border: 1px solid #cbd5e1; border-radius: 6px;">
            </div>
            <div>
                <label style="display: block; font-weight: bold; color: #475569; margin-bottom: 5px;">Altura (m ou cm):</label>
                <input type="number" step="0.01" name="altura" required style="width: 100%; padding: 10px; border: 1px solid #cbd5e1; border-radius: 6px;">
            </div>
            <button type="submit" style="background: #1e3a8a; color: white; padding: 12px; border: none; border-radius: 6px; font-weight: bold; cursor: pointer;">Calcular</button>
        </form>

        {f'''
        <div style="margin-top: 20px; padding: 15px; background: #f1f5f9; border-radius: 6px; border-left: 4px solid #1e3a8a;">
            <p style="margin: 0; font-weight: bold; color: #1e3a8a;">Resultado: {resultado[0]} kg/m²</p>
            <p style="margin: 5px 0 0 0; color: #475569;">Classificação: <b>{resultado[1]}</b></p>
        </div>
        ''' if resultado else ''}
    </div>
    """
    return render_template_string(base_html(), titulo="Calculadora — DV Odonto", conteudo=html_calc)
# -------------------------------------------------------------------
# 1. ROTA DE AGENDAMENTO DE CONSULTAS
# -------------------------------------------------------------------
@app.route('/agenda', methods=['GET', 'POST'])
@role_required("clinical")
def tela_agenda():
    if request.method == 'POST':
        paciente_id = request.form.get('paciente_id')
        data_hora = request.form.get('data_hora')
        procedimento = request.form.get('procedimento')
        especialidade = request.form.get('especialidade', 'Clínica Geral')

        if paciente_id and data_hora:
            database.adicionar_agendamento(paciente_id, data_hora, especialidade, procedimento, cnes_unidade=_active_cnes_unidade())
            return redirect(url_for('tela_agenda'))

    pacientes = database.list_pacientes(_active_cnes_unidade())
    compromissos = database.obter_agenda(_active_cnes_unidade())

    # Prepara lista de opções de pacientes
    opt_pacientes = "".join([f'<option value="{p["id"]}">{p["nome"]} (CNS: {p.get("cns", "N/I")})</option>' for p in pacientes])
    
    # Prepara lista de opções SIGTAP
    opt_sigtap = "".join([f'<option value="{p["codigo"]} - {p["nome"]}">{p["codigo"]} - {p["nome"]}</option>' for p in PROCEDIMENTOS_SIGTAP])

   # Adicionando uma coluna "Ações" nas linhas da tabela
    linhas_tabela = ""
    for comp in compromissos:
        p_nome = next((p["nome"] for p in pacientes if str(p["id"]) == str(comp.get("paciente_id"))), "Paciente Não Selecionado")
        dh = comp.get("data_hora", "").replace("T", " ")
        p_id = comp.get("paciente_id")
        
        linhas_tabela += f"""
        <tr>
            <td style="padding:10px; border-bottom:1px solid #e2e8f0;"><b>{dh}</b></td>
            <td style="padding:10px; border-bottom:1px solid #e2e8f0;">{p_nome}</td>
            <td style="padding:10px; border-bottom:1px solid #e2e8f0; color:#1e3a8a; font-weight:bold;">{comp.get("procedimento")}</td>
            <td style="padding:10px; border-bottom:1px solid #e2e8f0;">{comp.get("especialidade")}</td>
            <td style="padding:10px; border-bottom:1px solid #e2e8f0; text-align:center;">
                <a href="/odontograma?paciente_id={p_id}" style="background:#0284c7; color:white; padding:6px 10px; border-radius:4px; text-decoration:none; font-size:12px; font-weight:bold;">🦷 Odontograma</a>
            </td>
        </tr>
        """

    html_agenda = f"""
    <div style="background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
        
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; border-bottom: 2px solid #f1f5f9; padding-bottom: 12px;">
            <h2 style="color: #1e3a8a; margin: 0;">📅 Agendamento de Consultas — e-SUS</h2>
            <div style="display: flex; gap: 10px;">
                <button onclick="window.print()" style="background: #64748b; color: white; border: none; padding: 8px 14px; border-radius: 6px; font-weight: bold; cursor: pointer; font-size: 13px;">🖨️ Imprimir Agenda</button>
                <a href="/relatorio" style="background: #16a34a; color: white; border: none; padding: 8px 14px; border-radius: 6px; font-weight: bold; text-decoration: none; font-size: 13px;">📊 Ir para Produção e-SUS</a>
            </div>
        </div>
        
        <form method="POST" style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 25px; background: #f8fafc; padding: 15px; border-radius: 8px; border: 1px solid #e2e8f0;">
            <div>
                <label><b>Paciente*:</b></label>
                <select name="paciente_id" required style="width:100%; padding:8px; border-radius:4px; border:1px solid #cbd5e1;">
                    <option value="">Selecione o Paciente...</option>
                    {opt_pacientes}
                </select>
            </div>
            <div>
                <label><b>Data e Hora*:</b></label>
                <input type="datetime-local" name="data_hora" required style="width:100%; padding:8px; border-radius:4px; border:1px solid #cbd5e1;">
            </div>
            <div style="grid-column: span 2;">
                <label><b>Procedimento SIGTAP / SUS:</b></label>
                <select name="procedimento" style="width:100%; padding:8px; border-radius:4px; border:1px solid #cbd5e1;">
                    {opt_sigtap}
                </select>
            </div>
            <div style="grid-column: span 2;">
                <button type="submit" style="background: #1e3a8a; color: white; border: none; padding: 10px; border-radius: 6px; font-weight: bold; cursor: pointer; width: 100%;">Confirmar Agendamento</button>
            </div>
        </form>

        <h3 style="color: #1e3a8a;">Consultas Marcadas</h3>
        <table style="width: 100%; border-collapse: collapse; text-align: left;">
            <thead>
                <tr style="background-color: #f1f5f9;">
                    <th style="padding:10px; border-bottom:2px solid #cbd5e1;">Data / Hora</th>
                    <th style="padding:10px; border-bottom:2px solid #cbd5e1;">Paciente</th>
                    <th style="padding:10px; border-bottom:2px solid #cbd5e1;">Procedimento</th>
                    <th style="padding:10px; border-bottom:2px solid #cbd5e1;">Especialidade</th>
                    <th style="padding:10px; border-bottom:2px solid #cbd5e1; text-align:center;">Ações</th>
                </tr>
            </thead>
            <tbody>
                {linhas_tabela if linhas_tabela else '<tr><td colspan="5" style="padding:15px; text-align:center; color:#94a3b8;">Nenhum agendamento cadastrado.</td></tr>'}
            </tbody>
        </table>
    </div>
    """
# -------------------------------------------------------------------
# 2. ROTA DE RELATÓRIO DE PRODUÇÃO E-SUS / BPA
# -------------------------------------------------------------------
@app.route('/relatorio')
@role_required("clinical")
def tela_relatorio():
    conn = database.conectar()
    try:
        cnes_unidade = _active_cnes_unidade()
        dados = conn.execute(
            """
            SELECT codigo_sigtap, condicao, COUNT(*) as qtd
            FROM odontograma
            WHERE codigo_sigtap IS NOT NULL AND codigo_sigtap != ''
                AND (
                    ? IS NULL OR EXISTS (
                        SELECT 1 FROM pacientes
                        WHERE pacientes.id=odontograma.paciente_id
                            AND pacientes.cnes_unidade=?
                    )
                )
            GROUP BY codigo_sigtap
            """,
            (cnes_unidade, cnes_unidade),
        ).fetchall()
    finally:
        conn.close()

    linhas_relatorio = ""
    total_procedimentos = 0
    for row in dados:
        qtd = row["qtd"]
        total_procedimentos += qtd
        # Busca o nome correspondente na lista SIGTAP
        nome_proc = next((p["nome"] for p in PROCEDIMENTOS_SIGTAP if p["codigo"] == row["codigo_sigtap"]), row["condicao"])
        
        linhas_relatorio += f"""
        <tr>
            <td style="padding:12px; border-bottom:1px solid #e2e8f0; font-family:monospace; font-weight:bold;">{row['codigo_sigtap']}</td>
            <td style="padding:12px; border-bottom:1px solid #e2e8f0;">{nome_proc}</td>
            <td style="padding:12px; border-bottom:1px solid #e2e8f0; text-align:center; font-weight:bold; color:#1e3a8a;">{qtd}</td>
        </tr>
        """

    html_relatorio = f"""
    <div style="background: white; padding: 25px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
        <div style="display:flex; justify-shadow:space-between; align-items:center; border-bottom:2px solid #1e3a8a; padding-bottom:15px; margin-bottom:20px;">
            <div>
                <h2 style="color: #1e3a8a; margin:0;">📊 Relatório de Produção Ambulatorial (BPA / e-SUS)</h2>
                <p style="color: #64748b; margin:5px 0 0 0;">Consolidado de Atendimentos Odontológicos por Código SIGTAP</p>
            </div>
            <button onclick="window.print()" style="background:#0284c7; color:white; border:none; padding:10px 15px; border-radius:6px; cursor:pointer; font-weight:bold;">🖨️ Imprimir BPA</button>
        </div>

        <div style="display:grid; grid-template-columns: 1fr 1fr; gap:15px; margin-bottom:20px;">
            <div style="background:#f8fafc; padding:15px; border-radius:8px; border:1px solid #e2e8f0;">
                <span style="color:#64748b; font-size:12px; font-weight:bold;">TOTAL DE PROCEDIMENTOS</span>
                <h1 style="color:#1e3a8a; margin:5px 0 0 0;">{total_procedimentos}</h1>
            </div>
            <div style="background:#f8fafc; padding:15px; border-radius:8px; border:1px solid #e2e8f0;">
                <span style="color:#64748b; font-size:12px; font-weight:bold;">SITUAÇÃO DA REMESSA</span>
                <h3 style="color:#16a34a; margin:10px 0 0 0;">Pronto para Exportação e-SUS</h3>
            </div>
        </div>

        <table style="width: 100%; border-collapse: collapse; text-align: left;">
            <thead>
                <tr style="background-color: #f1f5f9;">
                    <th style="padding:12px; border-bottom:2px solid #cbd5e1;">Código SIGTAP</th>
                    <th style="padding:12px; border-bottom:2px solid #cbd5e1;">Descrição do Procedimento</th>
                    <th style="padding:12px; border-bottom:2px solid #cbd5e1; text-align:center;">Qtd. Realizada</th>
                </tr>
            </thead>
            <tbody>
                {linhas_relatorio if linhas_relatorio else '<tr><td colspan="3" style="padding:20px; text-align:center; color:#94a3b8;">Nenhum procedimento SIGTAP registrado até o momento.</td></tr>'}
            </tbody>
        </table>
    </div>
    """
    return render_template_string(base_html(), titulo="Relatório e-SUS — DV Odonto", conteudo=html_relatorio)

# ===================================================================
# ROTAS DE DOCUMENTAÇÃO E ARQUIVOS ESTÁTICOS
# ===================================================================

@app.route("/swagger.json")
def swagger_json():
    return app.send_static_file("swagger.json")


from dv_saude import dv_saude

app.extensions["dv_saude_base"] = base_html
app.register_blueprint(dv_saude)

# ===================================================================
# INICIALIZAÇÃO DO SERVIDOR (SISDCA)
# ===================================================================

if __name__ == "__main__":
    # Detecta automaticamente se está no ambiente de produção (Nuvem) ou Local
    is_cloud = "PORT" in os.environ
    port = int(os.environ.get("PORT", 5001))
    host = "0.0.0.0" if is_cloud else "127.0.0.1"

    print("\n" + "=" * 50)
    print("🚀 Servidor SisDCA Iniciado com Sucesso!")
    print(f"👉 Acesse localmente em: http://127.0.0.1:{port}/")
    print(f"📄 Swagger API: http://127.0.0.1:{port}/swagger.json")
    print("=" * 50 + "\n")

    # debug=False e use_reloader=False evitam o travamento de multiprocessing do Python 3.14 no Windows
    app.run(host=host, port=port, debug=False, use_reloader=False)