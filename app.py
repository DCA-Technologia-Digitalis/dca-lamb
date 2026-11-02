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
import hashlib
import os
import secrets
import uuid
import click
from datetime import date, datetime, timedelta
from functools import wraps
from collections import Counter
from ipaddress import ip_address
from threading import Lock
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

from flask import (
    abort,
    Flask,
    flash,
    jsonify,
    make_response,
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
from flask_session import Session
import redis

# ===================================================================
# 3. IMPORTAÇÕES DOS MÓDULOS LOCAIS (SisDCA)
# ===================================================================
import database
import commercial
import gerador_pdf
from api import auth_bp
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

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
UPLOAD_FOLDER = upload_dir

os.makedirs(static_dir, exist_ok=True)
os.makedirs(upload_dir, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__, static_folder=static_dir)
commercial.ensure_schema()
commercial.provision_super_admin()

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
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    SESSION_REFRESH_EACH_REQUEST=False,
    MAX_CONTENT_LENGTH=20 * 1024 * 1024,
)
redis_url = os.environ.get("REDIS_URL", "").strip()
if redis_url:
    app.config.update(
        SESSION_TYPE="redis",
        SESSION_REDIS=redis.Redis.from_url(
            redis_url, socket_connect_timeout=2, socket_timeout=2
        ),
        SESSION_KEY_PREFIX="dv-odonto:session:",
        SESSION_USE_SIGNER=True,
        SESSION_PERMANENT=True,
    )
    Session(app)
app.register_blueprint(auth_bp)

# Rate limiter — usa memória local (sem Redis); troque RATELIMIT_STORAGE_URL
# para redis://... em produção para compartilhar limites entre workers.
def _rate_limit_client():
    # Nginx overwrites X-Real-IP with the socket peer address before proxying.
    return request.headers.get("X-Real-IP", "").strip() or get_remote_address()


limiter = Limiter(
    _rate_limit_client,
    app=app,
    default_limits=[],
    storage_uri=os.environ.get("RATELIMIT_STORAGE_URL", "memory://"),
    strategy="fixed-window",
)
app.register_blueprint(commercial.commercial_bp)
app.view_functions["commercial.customer_register"] = limiter.limit("5 per hour")(
    app.view_functions["commercial.customer_register"]
)

_METRICS_LOCK = Lock()
_HTTP_REQUEST_COUNTS = Counter()
_HTTP_REQUEST_LATENCY_SUM = Counter()
_HTTP_REQUEST_LATENCY_COUNT = Counter()


@app.before_request
def start_request_timer():
    request.environ["dv_request_started_at"] = perf_counter()
    request.environ["dv_request_id"] = uuid.uuid4().hex


def _audit_event(action: str, resource_type: str, resource_id: Any = None) -> None:
    """Write minimal audit metadata; patient names and clinical details are excluded."""
    try:
        database.registrar_auditoria(
            session.get("user_id"),
            action,
            resource_type,
            resource_id,
            request.environ.get("dv_request_id"),
        )
    except Exception:
        app.logger.exception("Audit event could not be persisted")
        abort(503, description="Não foi possível registrar a auditoria desta operação.")


def _auth_csrf_token() -> str:
    token = session.get("_auth_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_auth_csrf"] = token
    return token


def _validate_auth_csrf(submitted_token: str) -> bool:
    expected_token = session.get("_auth_csrf", "")
    return bool(expected_token) and secrets.compare_digest(expected_token, submitted_token)


def _complete_login_session(user):
    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["user_nome"] = user["nome"]
    session["user_perfil"] = user["perfil"]
    session["cnes_unidade"] = user.get("cnes_unidade") or ""
    session["auth_version"] = user["session_version"]
    session["organization_id"] = user.get("organization_id")


def _begin_super_admin_mfa(user, next_url: str = ""):
    parsed = urlsplit(next_url or "")
    safe_next = next_url if (
        parsed.scheme == "" and parsed.netloc == "" and next_url.startswith("/")
        and not next_url.startswith("//")
    ) else "/dca/admin"
    session.clear()
    session["dca_mfa_pending_user_id"] = user["id"]
    session["dca_mfa_pending_version"] = user["session_version"]
    session["dca_mfa_next"] = safe_next
    session["dca_mfa_pending_mode"] = "verify" if commercial.mfa_enabled(user["id"]) else "setup"
    session["_auth_csrf"] = secrets.token_urlsafe(32)
    return redirect(url_for(
        "commercial.dca_mfa_verify" if session["dca_mfa_pending_mode"] == "verify"
        else "commercial.dca_mfa_setup"
    ))


@app.before_request
def protect_application_routes():
    if request.path.startswith("/static/uploads/"):
        abort(404)
    public_endpoints = {
        "static",
        "index",
        "login",
        "logout",
        "swagger_json",
        "pwa_manifest",
        "service_worker",
        "offline_page",
        "prometheus_metrics",
        "commercial.dca_portal",
        "commercial.customer_register",
        "commercial.dca_mfa_setup",
        "commercial.dca_mfa_verify",
    }
    if session.get("user_id"):
        user = database.buscar_usuario_por_id(session["user_id"])
        if not user or session.get("auth_version") != user["session_version"]:
            session.clear()
        else:
            # Roles and clinic assignments are refreshed from the database so
            # changes made by an administrator take effect on existing sessions.
            session["user_nome"] = user["nome"]
            session["user_perfil"] = user["perfil"]
            session["cnes_unidade"] = user.get("cnes_unidade") or ""
            session["organization_id"] = user.get("organization_id")

            if user.get("perfil") == "DCA_SUPER_ADMIN" and (
                not session.get("dca_mfa_authenticated") or not commercial.mfa_enabled(user["id"])
            ):
                session.clear()
                if request.path.startswith("/api/"):
                    return jsonify({"status": "error", "mensagem": "Autentica\u00e7\u00e3o multifator obrigat\u00f3ria."}), 401
                return redirect(url_for("login"))

            # New SaaS tenants cannot reach clinical or pharmacy routes while
            # their single organization subscription is pending. Legacy users
            # without a commercial subscription retain their existing access.
            protected_module_routes = (
                ("/farmacia-sus/estoque", "estoque"),
                ("/farmacia-sus/relatorios", "relatorios"),
                ("/farmacia-sus", "farma"),
                ("/odonto", "odonto"),
                ("/receita", "odonto"),
                ("/prontuario", "medico"),
                ("/pacientes", "prontuario"),
                ("/paciente", "prontuario"),
                ("/gerar_atestado", "prontuario"),
                ("/odontograma", "odonto"),
                ("/agenda", "prontuario"),
                ("/documentos", "prontuario"),
                ("/relatorio", "relatorios"),
                ("/calculadora", "prontuario"),
                ("/upload_exame", "prontuario"),
                ("/api/pacientes", "prontuario"),
            )
            required_module = next(
                (code for prefix, code in protected_module_routes
                 if request.path == prefix or request.path.startswith(prefix + "/")),
                None,
            )
            subscription_state = (
                commercial.subscription_status(user.get("organization_id"))
                if user.get("organization_id") and user.get("perfil") != "DCA_SUPER_ADMIN"
                else None
            )
            if required_module and subscription_state is not None and (
                subscription_state != "ACTIVE"
                or not commercial.module_enabled(user["organization_id"], required_module)
            ):
                if request.path.startswith("/api/"):
                    return jsonify({"status": "pending", "message": "A assinatura da organização aguarda confirmação."}), 402
                return redirect(url_for("commercial.customer_access_pending"))
    if (
        request.endpoint is not None
        and request.endpoint not in public_endpoints
        and not session.get("user_id")
    ):
        if request.path.startswith("/api/"):
            return jsonify({"status": "error", "mensagem": "Autenticação necessária."}), 401
        return redirect(url_for("login", next=request.url))

    if request.method not in {"GET", "HEAD", "OPTIONS", "TRACE"} and request.endpoint != "login":
        submitted_token = (
            request.form.get("csrf_token")
            or request.form.get("logout_csrf")
            or request.headers.get("X-CSRF-Token", "")
        )
        auth_token = session.get("_auth_csrf", "")
        module_token = session.get("_dv_csrf", "")
        valid_auth = bool(auth_token) and secrets.compare_digest(auth_token, submitted_token)
        valid_module = bool(module_token) and secrets.compare_digest(module_token, submitted_token)
        if not (valid_auth or valid_module):
            abort(400, description="Token CSRF inválido.")


@app.after_request
def apply_security_headers(response):
    response.headers.setdefault("X-Request-ID", request.environ.get("dv_request_id", ""))
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'self'; "
        "frame-ancestors 'none'; form-action 'self'",
    )
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"
    if request.endpoint != "prometheus_metrics":
        method = request.method
        endpoint = request.endpoint or "unknown"
        key = (method, endpoint, response.status_code)
        duration = max(
            0.0,
            perf_counter() - request.environ.get("dv_request_started_at", perf_counter()),
        )
        metrics_store = app.config.get("SESSION_REDIS")
        if metrics_store:
            try:
                prefix = f"dv:metrics:{method}:{endpoint}"
                pipeline = metrics_store.pipeline()
                pipeline.incr(f"{prefix}:requests:{response.status_code}")
                pipeline.incrbyfloat(f"{prefix}:duration_sum", duration)
                pipeline.incr(f"{prefix}:duration_count")
                pipeline.expire(f"{prefix}:requests:{response.status_code}", 2592000)
                pipeline.expire(f"{prefix}:duration_sum", 2592000)
                pipeline.expire(f"{prefix}:duration_count", 2592000)
                pipeline.execute()
            except redis.RedisError:
                app.logger.warning("Shared metrics storage unavailable; using worker-local counters")
                with _METRICS_LOCK:
                    _HTTP_REQUEST_COUNTS[key] += 1
                    _HTTP_REQUEST_LATENCY_SUM[(method, endpoint)] += duration
                    _HTTP_REQUEST_LATENCY_COUNT[(method, endpoint)] += 1
        else:
            with _METRICS_LOCK:
                _HTTP_REQUEST_COUNTS[key] += 1
                _HTTP_REQUEST_LATENCY_SUM[(method, endpoint)] += duration
                _HTTP_REQUEST_LATENCY_COUNT[(method, endpoint)] += 1
    return response


@app.route("/metrics", methods=["GET"])
def prometheus_metrics():
    try:
        requester = ip_address(request.remote_addr or "")
    except ValueError:
        abort(404)
    if not requester.is_private and not requester.is_loopback:
        abort(404)
    counts = []
    latency_sums = []
    latency_counts = []
    metrics_store = app.config.get("SESSION_REDIS")
    if metrics_store:
        try:
            for key in metrics_store.scan_iter(match="dv:metrics:*:requests:*"):
                parts = key.decode().split(":")
                if len(parts) == 6:
                    _, _, method, endpoint, _, status = parts
                    counts.append(((method, endpoint, int(status)), int(metrics_store.get(key) or 0)))
            for key in metrics_store.scan_iter(match="dv:metrics:*:duration_sum"):
                parts = key.decode().split(":")
                if len(parts) == 5:
                    _, _, method, endpoint, _ = parts
                    latency_sums.append(((method, endpoint), float(metrics_store.get(key) or 0)))
            for key in metrics_store.scan_iter(match="dv:metrics:*:duration_count"):
                parts = key.decode().split(":")
                if len(parts) == 5:
                    _, _, method, endpoint, _ = parts
                    latency_counts.append(((method, endpoint), int(metrics_store.get(key) or 0)))
        except (redis.RedisError, ValueError):
            app.logger.warning("Shared metrics could not be read; using worker-local counters")
    if not counts and not latency_sums and not latency_counts:
        with _METRICS_LOCK:
            counts = list(_HTTP_REQUEST_COUNTS.items())
            latency_sums = list(_HTTP_REQUEST_LATENCY_SUM.items())
            latency_counts = list(_HTTP_REQUEST_LATENCY_COUNT.items())
    lines = [
        "# HELP dv_http_requests_total HTTP requests served by Flask.",
        "# TYPE dv_http_requests_total counter",
    ]
    for (method, endpoint, status), count in sorted(counts):
        lines.append(
            f'dv_http_requests_total{{method="{method}",endpoint="{endpoint}",status="{status}"}} {count}'
        )
    lines.extend([
        "# HELP dv_http_request_duration_seconds_sum Total request time in seconds.",
        "# TYPE dv_http_request_duration_seconds_sum counter",
    ])
    for (method, endpoint), duration in sorted(latency_sums):
        lines.append(
            f'dv_http_request_duration_seconds_sum{{method="{method}",endpoint="{endpoint}"}} {duration:.6f}'
        )
    lines.extend([
        "# HELP dv_http_request_duration_seconds_count Number of timed requests.",
        "# TYPE dv_http_request_duration_seconds_count counter",
    ])
    for (method, endpoint), count in sorted(latency_counts):
        lines.append(
            f'dv_http_request_duration_seconds_count{{method="{method}",endpoint="{endpoint}"}} {count}'
        )
    return app.response_class(
        "\n".join(lines) + "\n",
        mimetype="text/plain; version=0.0.4; charset=utf-8",
    )

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
        return jsonify({"status": "error", "mensagem": "Perfil sem autorizacao clinica."}), 403
    if session.get("user_perfil") != "ADMIN" and not session.get("cnes_unidade"):
        return jsonify({"status": "error", "mensagem": "Conta sem unidade CNES vinculada."}), 403

    cnes_unidade = _active_cnes_unidade()
    if request.method == "POST":
        dados = request.get_json(silent=True)
        if not isinstance(dados, dict):
            return jsonify({"status": "error", "mensagem": "JSON invalido ou vazio."}), 400
        nome = (dados.get("nome") or "").strip()
        if not nome:
            return jsonify({"status": "error", "mensagem": "Nome obrigatorio."}), 400
        try:
            paciente_id = database.adicionar_paciente(
                nome=nome,
                telefone=dados.get("telefone", ""),
                cpf=dados.get("cpf", ""),
                cns=dados.get("cns", ""),
                data_nascimento=dados.get("data_nascimento", ""),
                sexo=dados.get("sexo", ""),
                bairro=dados.get("bairro", ""),
                pressao=dados.get("pressao", ""),
                peso=dados.get("peso", 0),
                altura=dados.get("altura", 0),
                cnes_unidade=cnes_unidade,
            )
        except (TypeError, ValueError) as error:
            return jsonify({"status": "error", "mensagem": str(error)}), 400
        _audit_event("patient_create", "patient", paciente_id)
        return jsonify({
            "status": "success",
            "mensagem": "Paciente cadastrado.",
            "paciente": database.get_paciente(paciente_id, cnes_unidade),
        }), 201

    _audit_event("patient_list", "patient")
    return jsonify({"status": "success", "pacientes": database.list_pacientes(cnes_unidade)})


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


ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".dcm"}


def _allowed_upload_name(filename: str) -> str | None:
    safe_name = secure_filename(filename or "")
    if not safe_name or os.path.splitext(safe_name)[1].lower() not in ALLOWED_UPLOAD_EXTENSIONS:
        return None
    return safe_name


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        ("Portal DCA", "/dca/", "portal", True),
        ("Minha organização", "/cliente/", "customer", session.get("user_perfil") == "ORG_ADMIN"),
        ("Super Admin DCA", "/dca/admin", "dca-admin", session.get("user_perfil") == "DCA_SUPER_ADMIN"),
        ("Visão geral", "/", "overview", True),
        ("DV Prontuário", "/prontuario/", "clinical", role_allows("clinical", session.get("user_perfil"))),
        ("DV Odonto", "/odonto/", "clinical", role_allows("clinical", session.get("user_perfil"))),
        ("DV Farmácia SUS", "/farmacia-sus/", "pharmacy", role_allows("pharmacy", session.get("user_perfil"))),
        ("DV Gestão Pública", "/gestao-publica/", "admin", role_allows("admin", session.get("user_perfil"))),
        ("DV Integrações", "/integracoes/", "admin", role_allows("admin", session.get("user_perfil"))),
        ("Configurações", "/configuracoes/", "admin", role_allows("admin", session.get("user_perfil"))),
        ("Usuários", "/usuarios", "admin", role_allows("admin", session.get("user_perfil"))),
        ("Minha Conta", "/minha-conta", "account", bool(session.get("user_id"))),
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
        <meta name="csrf-token" content="{escape(_auth_csrf_token())}">
        <title>{{{{ titulo }}}}</title>
        <link rel="manifest" href="/manifest.json">
        <meta name="theme-color" content="#1e3a8a">
        <meta name="apple-mobile-web-app-capable" content="yes">
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
        <meta name="apple-mobile-web-app-title" content="DV Saude">
        <link rel="apple-touch-icon" href="/static/logo.png">
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
            /* Indicador online/offline */
            #dv-status-bar {{ display:none; position:fixed; bottom:0; left:0; right:0; z-index:9999;
              padding:8px 16px; font-size:13px; font-weight:600; text-align:center; }}
            #dv-status-bar.offline {{ display:block; background:#dc2626; color:#fff; }}
            #dv-status-bar.syncing {{ display:block; background:#d97706; color:#fff; }}
            /* Botao instalar PWA */
            #dv-install-btn {{ display:none; position:fixed; bottom:16px; right:16px; z-index:9998;
              background:#1e3a8a; color:#fff; border:none; border-radius:10px;
              padding:12px 18px; font-size:14px; font-weight:700; cursor:pointer;
              box-shadow:0 4px 16px rgba(0,0,0,0.25); }}
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
        <div id="dv-status-bar"></div>
        <button id="dv-install-btn" title="Instalar DV Saude">Instalar app</button>
        <script>
        // Add CSRF protection to the existing server-rendered POST forms.
        const _csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
        document.querySelectorAll('form[method="post"], form[method="POST"]').forEach((form) => {{
          if (form.querySelector('input[name="csrf_token"], input[name="logout_csrf"]')) return;
          const input = document.createElement('input');
          input.type = 'hidden';
          input.name = 'csrf_token';
          input.value = _csrfToken;
          form.appendChild(input);
        }});
        // Service Worker
        if ('serviceWorker' in navigator) {{
          window.addEventListener('load', () => {{
            navigator.serviceWorker.register('/sw.js', {{scope: '/'}}).catch(() => {{}});
          }});
        }}
        // Indicador online/offline
        const _bar = document.getElementById('dv-status-bar');
        function _updateStatus() {{
          if (!navigator.onLine) {{
            _bar.textContent = 'Sem conexao com a internet — dados locais sendo usados';
            _bar.className = 'offline';
          }} else {{
            _bar.className = '';
          }}
        }}
        window.addEventListener('online', _updateStatus);
        window.addEventListener('offline', _updateStatus);
        _updateStatus();
        // Botao instalar PWA
        let _deferredPrompt;
        window.addEventListener('beforeinstallprompt', (e) => {{
          e.preventDefault();
          _deferredPrompt = e;
          document.getElementById('dv-install-btn').style.display = 'block';
        }});
        document.getElementById('dv-install-btn').addEventListener('click', async () => {{
          if (!_deferredPrompt) return;
          _deferredPrompt.prompt();
          await _deferredPrompt.userChoice;
          _deferredPrompt = null;
          document.getElementById('dv-install-btn').style.display = 'none';
        }});
        window.addEventListener('appinstalled', () => {{
          document.getElementById('dv-install-btn').style.display = 'none';
        }});
        </script>
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

@app.route("/manifest.json")
def pwa_manifest():
    """Serve o manifest PWA com cache-control adequado."""
    response = make_response(app.send_static_file("manifest.json"))
    response.headers["Content-Type"] = "application/manifest+json"
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@app.route("/sw.js")
def service_worker():
    """Serve o service worker na raiz para ter escopo total da aplicacao."""
    response = make_response(app.send_static_file("sw.js"))
    response.headers["Content-Type"] = "application/javascript"
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.route("/offline")
def offline_page():
    content = """
    <div style="text-align:center;padding:60px 20px;">
      <h1 style="color:#1e3a8a;">DV Saude</h1>
      <p style="color:#64748b;font-size:18px;">Voce esta sem conexao com a internet.</p>
      <p style="color:#94a3b8;">As funcionalidades essenciais continuam disponiveis.<br>
      Quando a conexao retornar, os dados serao sincronizados automaticamente.</p>
      <button onclick="location.reload()" style="margin-top:20px;padding:12px 24px;
        background:#1e3a8a;color:white;border:none;border-radius:8px;
        font-size:16px;cursor:pointer;">Tentar novamente</button>
    </div>
    """
    return render_template_string(
        base_html(), titulo="Sem conexao — DV Saude", conteudo=Markup(content)
    ), 503


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("20 per minute", methods=["POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("index"))
    if session.get("dca_mfa_pending_user_id"):
        target = "commercial.dca_mfa_verify" if session.get("dca_mfa_pending_mode") == "verify" else "commercial.dca_mfa_setup"
        return redirect(url_for(target))

    erro = ""
    modo = request.args.get("modo", "senha")  # senha | pin

    if request.method == "POST":
        if not _validate_auth_csrf(request.form.get("csrf_token", "")):
            abort(400, description="Token CSRF inv\u00e1lido.")
        modo = request.form.get("modo", "senha")

        if modo == "pin":
            usuario = request.form.get("usuario_pin", "").strip()
            pin = request.form.get("pin", "").strip()
            user = database.buscar_usuario_por_login(usuario)
            if not user:
                erro = "Usuário ou PIN incorretos."
            elif database.pin_esta_bloqueado(user["id"]):
                erro = "PIN bloqueado temporariamente. Aguarde 15 minutos ou use sua senha."
            elif not database.verificar_pin_usuario(user["id"], pin):
                erro = "Usuário ou PIN incorretos."
            elif user.get("perfil") == "DCA_SUPER_ADMIN":
                return _begin_super_admin_mfa(user, request.form.get("next", ""))
            else:
                _complete_login_session(user)
                _audit_event("login_success", "auth", user["id"])
                next_url = request.form.get("next", "") or url_for("index")
                parsed = urlsplit(next_url)
                if parsed.scheme == "" and parsed.netloc == "" and next_url.startswith("/"):
                    return redirect(next_url)
                return redirect(url_for("index"))
        else:
            usuario = request.form.get("usuario", "").strip()
            senha = request.form.get("senha", "")
            user = database.autenticar_usuario(usuario, senha)
            if not user:
                erro = "Usuário ou senha incorretos."
            elif user.get("perfil") == "DCA_SUPER_ADMIN":
                return _begin_super_admin_mfa(user, request.form.get("next", ""))
            else:
                _complete_login_session(user)
                _audit_event("login_success", "auth", user["id"])
                next_url = request.form.get("next", "") or url_for("index")
                parsed = urlsplit(next_url)
                if parsed.scheme == "" and parsed.netloc == "" and next_url.startswith("/"):
                    return redirect(next_url)
                return redirect(url_for("index"))

    if request.method == "POST" and erro:
        _audit_event("login_failure", "auth")
    next_url = escape(request.args.get("next", ""))
    erro_html = f'<p class="login-erro" role="alert">{escape(erro)}</p>' if erro else ""

    content = f"""
    <div class="login-wrap">
      <div class="login-card">
        <div class="login-logo">
          <img src="/static/logo.png" alt="Logo" onerror="this.style.display='none'">
          <h1>DV <span>Saúde</span></h1>
          <p>Sistema Integrado de Saúde</p>
        </div>

        <div class="login-tabs">
          <button type="button" class="tab-btn {'active' if modo == 'senha' else ''}" onclick="trocarModo('senha')">Senha</button>
          <button type="button" class="tab-btn {'active' if modo == 'pin' else ''}" onclick="trocarModo('pin')">PIN</button>
        </div>

        {erro_html}

        <form id="form-senha" method="POST" class="login-form" style="display:{'block' if modo != 'pin' else 'none'}">
          <input type="hidden" name="modo" value="senha">
          <input type="hidden" name="csrf_token" value="{escape(_auth_csrf_token())}">
          <input type="hidden" name="next" value="{next_url}">
          <label>
            <span>Usuário</span>
            <input type="text" name="usuario" autocomplete="username" required placeholder="Digite seu usuário">
          </label>
          <label>
            <span>Senha</span>
            <div class="input-eye">
              <input type="password" id="campo-senha" name="senha" autocomplete="current-password" required placeholder="Digite sua senha">
              <button type="button" class="eye-btn" onclick="toggleSenha('campo-senha', this)">&#128065;</button>
            </div>
          </label>
          <button type="submit" class="btn-entrar">Entrar</button>
        </form>

        <form id="form-pin" method="POST" class="login-form" style="display:{'block' if modo == 'pin' else 'none'}">
          <input type="hidden" name="modo" value="pin">
          <input type="hidden" name="csrf_token" value="{escape(_auth_csrf_token())}">
          <input type="hidden" name="next" value="{next_url}">
          <label>
            <span>Usuário</span>
            <input type="text" name="usuario_pin" autocomplete="username" required placeholder="Digite seu usuário">
          </label>
          <label>
            <span>PIN (4 dígitos)</span>
            <input type="password" name="pin" inputmode="numeric" pattern="[0-9]{{4}}" maxlength="4" autocomplete="off" required placeholder="0000">
          </label>
          <button type="submit" class="btn-entrar">Desbloquear</button>
        </form>

        <p class="login-rodape">DV Saúde &mdash; Acesso restrito a profissionais autorizados</p>
        <p class="login-rodape">Cliente ou profissional? <a href="/dca/">Conheça a plataforma DCA Software</a></p>
      </div>
    </div>

    <style>
      body {{ background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 100%); min-height: 100vh; }}
      .login-wrap {{ display: flex; align-items: center; justify-content: center; min-height: 80vh; padding: 20px; }}
      .login-card {{ background: #fff; border-radius: 18px; padding: 40px 36px; width: 100%; max-width: 400px; box-shadow: 0 20px 60px rgba(0,0,0,0.35); }}
      .login-logo {{ text-align: center; margin-bottom: 24px; }}
      .login-logo img {{ height: 56px; margin-bottom: 8px; }}
      .login-logo h1 {{ margin: 0; font-size: 28px; color: #0f172a; }}
      .login-logo h1 span {{ color: #2563eb; }}
      .login-logo p {{ margin: 4px 0 0; color: #64748b; font-size: 13px; }}
      .login-tabs {{ display: flex; gap: 0; border: 1px solid #e2e8f0; border-radius: 10px; overflow: hidden; margin-bottom: 22px; }}
      .tab-btn {{ flex: 1; padding: 10px; border: none; background: #f8fafc; color: #64748b; font-weight: 600; cursor: pointer; font-size: 14px; transition: all .2s; }}
      .tab-btn.active {{ background: #1e3a8a; color: #fff; }}
      .login-form label {{ display: block; margin-bottom: 16px; }}
      .login-form label span {{ display: block; font-size: 13px; font-weight: 600; color: #475569; margin-bottom: 6px; }}
      .login-form input {{ width: 100%; padding: 12px 14px; border: 1.5px solid #e2e8f0; border-radius: 9px; font-size: 15px; box-sizing: border-box; transition: border-color .2s; }}
      .login-form input:focus {{ outline: none; border-color: #2563eb; box-shadow: 0 0 0 3px #dbeafe; }}
      .input-eye {{ position: relative; }}
      .input-eye input {{ padding-right: 44px; }}
      .eye-btn {{ position: absolute; right: 10px; top: 50%; transform: translateY(-50%); border: none; background: transparent; cursor: pointer; font-size: 16px; color: #94a3b8; }}
      .btn-entrar {{ width: 100%; padding: 13px; background: #1e3a8a; color: #fff; border: none; border-radius: 9px; font-size: 16px; font-weight: 700; cursor: pointer; margin-top: 4px; transition: background .2s; }}
      .btn-entrar:hover {{ background: #1d4ed8; }}
      .login-erro {{ background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; border-radius: 8px; padding: 10px 14px; font-size: 14px; margin-bottom: 16px; }}
      .login-rodape {{ text-align: center; color: #94a3b8; font-size: 12px; margin-top: 24px; }}
    </style>

    <script>
    function trocarModo(modo) {{
      document.getElementById('form-senha').style.display = modo === 'senha' ? 'block' : 'none';
      document.getElementById('form-pin').style.display = modo === 'pin' ? 'block' : 'none';
      document.querySelectorAll('.tab-btn').forEach((b, i) => {{
        b.classList.toggle('active', (i === 0 && modo === 'senha') || (i === 1 && modo === 'pin'));
      }});
    }}
    function toggleSenha(id, btn) {{
      const campo = document.getElementById(id);
      if (campo.type === 'password') {{ campo.type = 'text'; btn.style.opacity = '1'; }}
      else {{ campo.type = 'password'; btn.style.opacity = '0.5'; }}
    }}
    </script>
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


@app.route("/minha-conta", methods=["GET", "POST"])
def minha_conta():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    user_id = session["user_id"]
    mensagem = ""
    erro = ""

    if request.method == "POST":
        if not _validate_auth_csrf(request.form.get("csrf_token", "")):
            return "Token de segurança inválido.", 400
        acao = request.form.get("acao", "")

        if acao == "alterar_senha":
            senha_atual = request.form.get("senha_atual", "")
            nova_senha = request.form.get("nova_senha", "")
            confirmar = request.form.get("confirmar_senha", "")
            user = database.autenticar_usuario(session["user_nome"], senha_atual)
            # Tenta também pelo campo usuario da sessão
            if not user:
                u = database.buscar_usuario_por_id(user_id)
                user = database.autenticar_usuario(u["usuario"], senha_atual) if u else None
            if not user:
                erro = "Senha atual incorreta."
            elif nova_senha != confirmar:
                erro = "As senhas não coincidem."
            else:
                try:
                    database.definir_senha_usuario(user_id, nova_senha)
                    mensagem = "Senha alterada com sucesso."
                except ValueError as e:
                    erro = str(e)

        elif acao == "definir_pin":
            pin = request.form.get("pin", "").strip()
            confirmar_pin = request.form.get("confirmar_pin", "").strip()
            if pin != confirmar_pin:
                erro = "Os PINs não coincidem."
            else:
                try:
                    database.definir_pin_usuario(user_id, pin)
                    mensagem = "PIN definido com sucesso."
                except ValueError as e:
                    erro = str(e)

    csrf = escape(_auth_csrf_token())
    content = f"""
    <section class="hero"><p class="eyebrow">CONTA</p>
    <h1>Minha Conta</h1>
    <p>Altere sua senha ou configure um PIN de acesso rápido.</p></section>
    {f'<p class="notice">{escape(mensagem)}</p>' if mensagem else ''}
    {f'<p class="error">{escape(erro)}</p>' if erro else ''}

    <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:20px; margin-top:20px;">

      <div class="panel">
        <h3 style="color:#1e3a8a; margin-top:0;">Alterar Senha</h3>
        <form method="POST" style="display:grid; gap:12px;">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <input type="hidden" name="acao" value="alterar_senha">
          <label style="font-size:13px; font-weight:600; color:#475569;">Senha atual
            <input type="password" name="senha_atual" required style="display:block; width:100%; margin-top:4px; padding:10px; border:1px solid #cbd5e1; border-radius:7px;">
          </label>
          <label style="font-size:13px; font-weight:600; color:#475569;">Nova senha (mín. 6 caracteres)
            <input type="password" name="nova_senha" minlength="6" required style="display:block; width:100%; margin-top:4px; padding:10px; border:1px solid #cbd5e1; border-radius:7px;">
          </label>
          <label style="font-size:13px; font-weight:600; color:#475569;">Confirmar nova senha
            <input type="password" name="confirmar_senha" minlength="6" required style="display:block; width:100%; margin-top:4px; padding:10px; border:1px solid #cbd5e1; border-radius:7px;">
          </label>
          <button class="button" type="submit">Salvar nova senha</button>
        </form>
      </div>

      <div class="panel">
        <h3 style="color:#1e3a8a; margin-top:0;">PIN de Acesso Rápido</h3>
        <p style="color:#64748b; font-size:13px;">O PIN de 4 dígitos permite desbloqueio rápido. Bloqueado após 5 tentativas erradas por 15 minutos.</p>
        <form method="POST" style="display:grid; gap:12px;">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <input type="hidden" name="acao" value="definir_pin">
          <label style="font-size:13px; font-weight:600; color:#475569;">PIN (4 dígitos)
            <input type="password" name="pin" inputmode="numeric" pattern="[0-9]{{4}}" maxlength="4" required style="display:block; width:100%; margin-top:4px; padding:10px; border:1px solid #cbd5e1; border-radius:7px; letter-spacing:8px; font-size:20px;">
          </label>
          <label style="font-size:13px; font-weight:600; color:#475569;">Confirmar PIN
            <input type="password" name="confirmar_pin" inputmode="numeric" pattern="[0-9]{{4}}" maxlength="4" required style="display:block; width:100%; margin-top:4px; padding:10px; border:1px solid #cbd5e1; border-radius:7px; letter-spacing:8px; font-size:20px;">
          </label>
          <button class="button" type="submit">Salvar PIN</button>
        </form>
      </div>

    </div>
    """
    return render_template_string(
        base_html(), titulo="Minha Conta — DV Saúde", conteudo=Markup(content)
    )


@app.route("/usuarios", methods=["GET", "POST"])
@role_required("admin")
def usuarios_page():
    message = ""
    invite_code = ""
    if request.method == "POST":
        if not _validate_auth_csrf(request.form.get("csrf_token", "")):
            return "Token de segurança inválido. Atualize a página e tente novamente.", 400
        if request.form.get("acao") == "convidar":
            try:
                user_id = int(request.form.get("user_id", ""))
            except ValueError:
                message = "Usuário inválido."
            else:
                invite_code = database.criar_convite_passkey(user_id) or ""
                if not invite_code:
                    message = "Não foi possível gerar o convite para esse usuário."
        else:
            try:
                created = database.cadastrar_usuario(
                    request.form.get("nome", ""),
                    request.form.get("usuario", ""),
                    request.form.get("perfil", ""),
                    request.form.get("registro_profissional", ""),
                    request.form.get("cnes_unidade", ""),
                )
            except ValueError as error:
                message = str(error)
            else:
                if created:
                    user = database.buscar_usuario_por_login(
                        request.form.get("usuario", "").strip()
                    )
                    _audit_event("admin_user_create", "user", user["id"])
                    invite_code = database.criar_convite_passkey(user["id"]) or ""
                    if not invite_code:
                        message = "Conta criada, mas não foi possível gerar o convite."
                else:
                    message = "Esse nome de usuário já está cadastrado."

    users = database.listar_usuarios()
    units = database.listar_unidades()
    unit_names = {unit["cnes"]: unit["nome"] for unit in units}
    rows = "".join(
        f"<tr><td>{escape(user['nome'])}</td><td>{escape(user['usuario'])}</td>"
        f"<td>{escape(user['perfil'])}</td><td>{escape(user['registro_profissional'] or '')}</td>"
        f"<td>{escape(unit_names.get(user.get('cnes_unidade'), 'Acesso global'))}</td>"
        f"<td>{user['passkey_count']}</td><td><form method='post'>"
        f"<input type='hidden' name='csrf_token' value='{escape(_auth_csrf_token())}'>"
        f"<input type='hidden' name='acao' value='convidar'>"
        f"<input type='hidden' name='user_id' value='{user['id']}'>"
        f"<button class='button' type='submit'>Emitir convite</button></form></td></tr>"
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
      <p>Crie contas sem senha e emita convites de uso único após validar a identidade da pessoa.
      Ao concluir o convite, as passkeys antigas dessa conta serão substituídas.</p></section>
      <form class="panel form-grid" method="post">
        <input type="hidden" name="csrf_token" value="{escape(_auth_csrf_token())}">
        <label>Nome<input name="nome" maxlength="160" autocomplete="name" required></label>
        <label>Usuário<input name="usuario" maxlength="80" autocomplete="username" required></label>
        <label>Perfil<select name="perfil" required>{profile_options}</select></label>
        <label>Unidade de saúde<select name="cnes_unidade">{unit_options}</select></label>
        <label>Registro profissional<input name="registro_profissional" maxlength="80"></label>
        <button class="button" type="submit">Cadastrar usuário</button>
      </form>
      {f'<p class="error">{escape(message)}</p>' if message else ''}
      {f'<section class="notice"><strong>Convite de uso único — válido por 60 minutos:</strong><br><code>{escape(invite_code)}</code><br>Compartilhe com o usuário por um canal confiável. O código não será exibido novamente.</section>' if invite_code else ''}
      <div class="table-wrap"><table><thead><tr><th>Nome</th><th>Usuário</th>
      <th>Perfil</th><th>Registro</th><th>Unidade</th><th>Passkeys</th>
      <th>Convite</th></tr></thead><tbody>{rows}</tbody></table></div>
    """
    response = make_response(
        render_template_string(
            base_html(), titulo="Usuários — DV Saúde", conteudo=Markup(content)
        )
    )
    if invite_code:
        response.headers["Cache-Control"] = "no-store"
    return response
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
            if database.salvar_soap(paciente_id, subjetivo, objetivo, avaliacao, plano, cnes_unidade=_active_cnes_unidade()):
                _audit_event("soap_create", "soap", paciente_id)
            return redirect(url_for('prontuario_page', paciente_id=paciente_id))

    pacientes = database.list_pacientes(_active_cnes_unidade())
    paciente_atual = next((p for p in pacientes if str(p.get("id")) == str(paciente_id)), None) if paciente_id else None
    if paciente_atual:
        _audit_event("patient_view", "patient", paciente_atual["id"])
    
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
        _prof = database.buscar_usuario_por_id(session['user_id']) or {}
        pdf_buffer = gerador_pdf.gerar_receita_pdf(
            paciente_nome, medicamentos, orientacoes,
            nome_profissional=_prof.get('nome', ''),
            cro=_prof.get('registro_profissional', ''),
        )
        
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
    if session.get("user_perfil") == "ORG_ADMIN":
        return redirect(url_for("commercial.customer_dashboard"))
    if session.get("user_perfil") == "DCA_SUPER_ADMIN":
        return redirect(url_for("commercial.super_admin_dashboard"))
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
        pressao = request.form.get("pressao") or ""
        try:
            altura = float(str(request.form.get("altura") or "0").replace(',', '.'))
            peso = float(str(request.form.get("peso") or "0").replace(',', '.'))
            paciente_id = database.adicionar_paciente(nome, telefone, cpf, peso=peso, altura=altura, pressao=pressao, cnes_unidade=_active_cnes_unidade())
        except ValueError:
            return redirect(url_for("pacientes_page", erro="dados_invalidos"))
        _audit_event("patient_create", "patient", paciente_id)
        return redirect("/pacientes")

    busca = request.args.get("busca", "").strip()
    try:
        pagina = max(1, int(request.args.get("pagina", "1")))
    except (TypeError, ValueError):
        pagina = 1
    erro_cadastro = request.args.get("erro") == "dados_invalidos"
    try:
        lista_pacientes, total_pacientes = database.list_pacientes_paginado(
            _active_cnes_unidade(), pagina, 50, busca
        )
        if not isinstance(lista_pacientes, list):
            lista_pacientes = []
    except Exception:
        lista_pacientes = []
        total_pacientes = 0
    _audit_event("patient_list", "patient")

    linhas_pacientes = ""
    for p in lista_pacientes:
        if isinstance(p, dict):
            p_id = p.get("id", "")
            p_nome = escape(p.get("nome") or "")
            p_tel = escape(p.get("telefone") or "-")
            p_cpf = escape(p.get("cpf") or "-")
            p_pressao = escape(str(p.get("pressao") or "-"))
            p_peso = escape(str(p.get("peso") or "-"))
            p_altura = escape(str(p.get("altura") or "-"))
            p_imc = escape(str(p.get("imc") or "-"))
            p_classificacao_imc = escape(str(p.get("classificacao_imc") or "-"))
        else:
            try:
                p_id = p[0] if len(p) > 0 else ""
                p_nome = escape(str(p[1])) if len(p) > 1 else ""
                p_tel = escape(str(p[2])) if len(p) > 2 else "-"
                p_cpf = escape(str(p[3])) if len(p) > 3 else "-"
                p_pressao = escape(str(p[7])) if len(p) > 7 else "-"
                p_peso = escape(str(p[8])) if len(p) > 8 else "-"
                p_altura = escape(str(p[9])) if len(p) > 9 else "-"
                p_imc = escape(str(p[10])) if len(p) > 10 else "-"
                p_classificacao_imc = escape(str(p[11])) if len(p) > 11 else "-"
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

    busca_escaped = escape(busca)
    total_paginas = max(1, (total_pacientes + 49) // 50)
    nav_pacinas = '<nav style="display:flex;gap:12px;justify-content:center;padding:16px">'
    if pagina > 1:
        nav_pacinas += f'<a href="{url_for("pacientes_page", pagina=pagina - 1, busca=busca)}">Anterior</a>'
    nav_pacinas += f'<span>Página {pagina} de {total_paginas} ({total_pacientes} pacientes)</span>'
    if pagina < total_paginas:
        nav_pacinas += f'<a href="{url_for("pacientes_page", pagina=pagina + 1, busca=busca)}">Próxima</a>'
    nav_pacinas += '</nav>'
    html_pacientes = f"""
    <div style="background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
        <h2 style="color: #1e3a8a; margin-top: 0;">Pacientes Cadastrados</h2>
        {('<p class="error">Verifique os dados informados e tente novamente.</p>' if erro_cadastro else '')}
        <form method="GET" style="margin-bottom:16px; display:flex; gap:10px; align-items:center;">
            <input type="text" name="busca" value="{busca_escaped}" placeholder="🔍 Buscar por nome ou CPF..." style="flex:1; padding:10px; border:1px solid #cbd5e1; border-radius:8px;">
            <button type="submit" style="background:#1e3a8a; color:white; border:none; padding:10px 18px; border-radius:8px; cursor:pointer; font-weight:bold;">Buscar</button>
            {f'<a href="/pacientes" style="color:#64748b; font-size:13px; text-decoration:none;">Limpar</a>' if busca else ''}
        </form>
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
                {linhas_pacientes if linhas_pacientes else '<tr><td colspan="10" style="padding:20px; text-align:center; color:#94a3b8;">Nenhum paciente encontrado.</td></tr>'}
            </tbody>
        </table>
        {nav_pacinas}
    </div>
    """
    return render_template_string(base_html(), titulo="Pacientes — DV Odonto", conteudo=html_pacientes)

@app.route('/paciente/<int:paciente_id>/editar', methods=['GET', 'POST'])
@role_required("clinical")
def editar_paciente(paciente_id: int) -> Any:
    paciente = database.get_paciente(paciente_id, _active_cnes_unidade())
    if not paciente:
        return redirect('/pacientes')
    _audit_event("patient_view", "patient", paciente_id)

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
        _audit_event("patient_update", "patient", paciente_id)
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
    _prof = database.buscar_usuario_por_id(session['user_id']) or {}
    pdf_buffer = gerador_pdf.gerar_atestado_pdf(
        paciente_nome, dias, motivo,
        nome_profissional=_prof.get('nome', ''),
        cro=_prof.get('registro_profissional', ''),
    )
    
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
    if paciente:
        _audit_event("patient_view", "patient", paciente["id"])
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
                    original_name = _allowed_upload_name(file.filename)
                    if not original_name:
                        mensagem = 'Tipo de arquivo não permitido. Use PDF, JPG, PNG ou DICOM.'
                    else:
                        stored_name = f"paciente_{paciente_id}_{uuid.uuid4().hex[:8]}_{original_name}"
                        filepath = os.path.join(app.config['UPLOAD_FOLDER'], stored_name)
                        file.save(filepath)
                        mime_type = file.mimetype or 'application/octet-stream'
                        saved = database.adicionar_arquivo(
                            int(paciente_id), tipo, original_name, stored_name,
                            mime_type, descricao, cnes_unidade=_active_cnes_unidade(),
                            sha256=_sha256_file(filepath),
                            actor_user_id=session.get("user_id"),
                            request_id=request.environ.get("dv_request_id"),
                        )
                        if saved:
                            mensagem = f"Arquivo '{escape(original_name)}' carregado com sucesso."
                        else:
                            os.unlink(filepath)
                            mensagem = 'Não foi possível vincular o arquivo ao paciente.'
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
                database.adicionar_arquivo(
                    int(paciente_id), tipo, original_name, stored_name, "text/plain",
                    f"Termo automático gerado: {procedimento}",
                    cnes_unidade=_active_cnes_unidade(), sha256=_sha256_file(filepath),
                    actor_user_id=session.get("user_id"),
                    request_id=request.environ.get("dv_request_id"),
                    audit_action="document_create",
                )
                mensagem = f"Termo '{tipo}' gerado e salvo."  

    arquivos_html = ''
    if paciente:
        arquivos = database.listar_arquivos_paciente(int(paciente_id), _active_cnes_unidade()) # type: ignore
        if arquivos:
            for arq in arquivos:
                descricao_text = f" — {arq.get('descricao', '')}" if arq.get('descricao') else ''
                arquivos_html += f"<div style=\"display:flex; justify-content:space-between; align-items:center; padding:10px; background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; margin-bottom:8px;\">"
                arquivos_html += f"<div><strong>{escape(arq.get('tipo', ''))}</strong>: {escape(arq.get('nome_original', ''))}<br><span style='font-size:13px; color:#475569;'>{escape(arq.get('criado_em', ''))}{descricao_text}</span></div>"
                file_url = url_for(
                    "baixar_arquivo_paciente",
                    paciente_id=int(paciente_id),
                    nome_stored=arq.get("nome_stored", ""),
                )
                arquivos_html += f"<a href=\"{escape(file_url)}\" style=\"color:#1e3a8a; text-decoration:none; font-weight:bold; font-size:14px;\">Baixar</a></div>"
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
        {f'<pre style="white-space:pre-wrap; word-break:break-word; background:#f8fafc; border:1px solid #cbd5e1; border-radius:8px; padding:15px; margin-top:20px;">{escape(termo_text)}</pre>' if termo_text else ''}
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
            saved = database.salvar_condicao_dente(paciente_id, dente, "geral", "extraido", cnes_unidade=_active_cnes_unidade())
        else:
            saved = database.salvar_condicao_dente(paciente_id, dente, face, condicao, cnes_unidade=_active_cnes_unidade())
        if saved:
            _audit_event("odontogram_update", "odontogram", paciente_id)
            
        return redirect(f"/odontograma?paciente_id={paciente_id}")

    if paciente_id:
        _audit_event("patient_view", "patient", paciente_id)

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
    if paciente_id and str(paciente_id).isdigit():
        try:
            arquivos_paciente = database.listar_arquivos_paciente(
                int(paciente_id), _active_cnes_unidade()
            )
            if arquivos_paciente:
                for arquivo in arquivos_paciente:
                    nome_exibicao = escape(arquivo.get("nome_original", "Arquivo"))
                    url_arquivo = url_for(
                        "baixar_arquivo_paciente",
                        paciente_id=int(paciente_id),
                        nome_stored=arquivo.get("nome_stored", ""),
                    )
                    arquivos_html += (
                        '<div style="display:flex; justify-content:space-between; align-items:center; '
                        'padding:10px; background:#f8fafc; border:1px solid #e2e8f0; '
                        'border-radius:6px; margin-bottom:8px;">'
                        f'<span style="color:#334155; font-size:14px;">?? {nome_exibicao}</span>'
                        f'<a href="{escape(url_arquivo)}" style="color:#1e3a8a; text-decoration:none; '
                        'font-weight:bold; font-size:14px;">Baixar</a></div>'
                    )
            else:
                arquivos_html = '<p class="empty">Nenhum exame ou laudo anexado para este paciente.</p>'
        except Exception:
            arquivos_html = '<p class="empty">Diretório de anexos indisponível.</p>'
            app.logger.exception("Falha ao listar anexos do paciente")


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
        if whatsapp:
            link_wa = whatsapp.gerar_link_lembrete_paciente(
                nome_paciente=nome_paciente,
                telefone=telefone,
                data_hora=dh_normalizado,
                procedimento=procedimento
            )
        else:
            link_wa = f"https://wa.me/{telefone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')}" if telefone else "#"

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


@app.route('/calculadora', methods=['GET', 'POST'])
@role_required("clinical")
def calculadora_page():
    resultado = None
    if request.method == 'POST':
        try:
            peso = request.form.get('peso')
            altura = request.form.get('altura')
            if peso and altura:
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
# ROTA DE RELATÓRIO DE PRODUÇÃO E-SUS / BPA
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
# ROTA DE UPLOAD DE EXAMES / ARQUIVOS
# ===================================================================

@app.route('/paciente/<int:paciente_id>/arquivo/<path:nome_stored>', methods=['GET'])
@role_required("clinical")
def baixar_arquivo_paciente(paciente_id: int, nome_stored: str):
    paciente = database.get_paciente(paciente_id, _active_cnes_unidade())
    if not paciente:
        abort(404)
    _audit_event("patient_view", "patient", paciente_id)

    arquivo = next(
        (
            item
            for item in database.listar_arquivos_paciente(
                paciente_id, _active_cnes_unidade()
            )
            if item.get("nome_stored") == nome_stored
        ),
        None,
    )
    if not arquivo:
        abort(404)

    safe_stored_name = secure_filename(nome_stored)
    if safe_stored_name != nome_stored:
        abort(404)
    private_path = os.path.join(app.config["UPLOAD_FOLDER"], safe_stored_name)
    legacy_path = os.path.join(static_dir, "uploads", safe_stored_name)
    filepath = private_path if os.path.isfile(private_path) else legacy_path
    if not os.path.isfile(filepath):
        abort(404)

    _audit_event("document_download", "document", arquivo["id"])

    response = send_file(
        filepath,
        mimetype="application/octet-stream",
        as_attachment=True,
        download_name=secure_filename(arquivo.get("nome_original") or safe_stored_name),
        conditional=True,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.route('/upload_exame', methods=['POST'])
@role_required("clinical")
def upload_exame():
    paciente_id = request.form.get('paciente_id', '').strip()
    next_url = request.form.get('next', '/documentos')
    parsed_next_url = urlsplit(next_url)
    if parsed_next_url.scheme or parsed_next_url.netloc or not next_url.startswith('/'):
        next_url = '/documentos'
    tipo = request.form.get('tipo', 'Exame')
    descricao = request.form.get('descricao', '')

    if not paciente_id or not paciente_id.isdigit():
        return redirect('/documentos')

    paciente = database.get_paciente(int(paciente_id), _active_cnes_unidade())
    if not paciente:
        return redirect('/documentos')

    file = request.files.get('file')
    if not file or file.filename == '':
        return redirect(next_url)

    original_name = _allowed_upload_name(file.filename)
    if not original_name:
        abort(400, description="Tipo de arquivo não permitido.")
    stored_name = f"paciente_{paciente_id}_{uuid.uuid4().hex[:8]}_{original_name}"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], stored_name)
    file.save(filepath)
    mime_type = file.mimetype or 'application/octet-stream'
    saved = database.adicionar_arquivo(
        int(paciente_id), tipo, original_name, stored_name,
        mime_type, descricao, cnes_unidade=_active_cnes_unidade(),
        sha256=_sha256_file(filepath), actor_user_id=session.get("user_id"),
        request_id=request.environ.get("dv_request_id"),
    )
    if not saved:
        os.unlink(filepath)
        abort(404)
    return redirect(next_url)


# ===================================================================
# ROTA DE EXCLUSÃO DE AGENDAMENTO
# ===================================================================

@app.route('/agenda/<int:agendamento_id>/excluir', methods=['POST'])
@role_required("clinical")
def excluir_agendamento(agendamento_id: int):
    if database.excluir_agendamento(agendamento_id, _active_cnes_unidade()):
        _audit_event("appointment_delete", "appointment", agendamento_id)
    return redirect(url_for('agenda_page'))


# ===================================================================
# ROTAS DE DOCUMENTAÇÃO E ARQUIVOS ESTÁTICOS
# ===================================================================

@app.route("/swagger.json")
def swagger_json():
    return app.send_static_file("swagger.json")


from dv_saude import dv_saude

app.extensions["dv_saude_base"] = base_html
app.register_blueprint(dv_saude)
@app.route("/healthz", methods=["GET"])
def healthz():
    """Endpoint de health check para Docker, Nginx e monitoramento."""
    status = {"status": "ok", "service": "dv-saude"}
    http_status = 200
    try:
        conn = database.conectar()
        try:
            conn.execute("SELECT 1").fetchone()
            status["database"] = "ok"
        finally:
            conn.close()
    except Exception:  # pragma: no cover - caminho de falha
        app.logger.exception("Health check could not reach the database")
        status["status"] = "degraded"
        status["database"] = "error"
        http_status = 503
    return jsonify(status), http_status




# ===================================================================
# INICIALIZAÇÃO DO SERVIDOR (SISDCA)
# ===================================================================

if __name__ == "__main__":
    # Detecta automaticamente se está no ambiente de produção (Nuvem) ou Local
    is_cloud = "PORT" in os.environ
    port = int(os.environ.get("PORT", 5001))
    host = "0.0.0.0" if is_cloud else "127.0.0.1"

    print("\n" + "=" * 50)
    print("Servidor SisDCA Iniciado com Sucesso!")
    print(f"Acesse localmente em: http://127.0.0.1:{port}/")
    print(f"Swagger API: http://127.0.0.1:{port}/swagger.json")
    print("=" * 50 + "\n")

    # debug=False e use_reloader=False evitam o travamento de multiprocessing do Python 3.14 no Windows
    app.run(host=host, port=port, debug=False, use_reloader=False)
