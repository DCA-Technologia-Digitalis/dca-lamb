"""
database.py — Gerenciamento do Banco de Dados SQLite (DV Odonto & Saúde Multiprofissional)
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from werkzeug.security import check_password_hash, generate_password_hash

DB_NAME = os.environ.get(
    "DV_DATABASE_PATH",
    str(Path(__file__).resolve().with_name("dv_odonto.db")),
)
LEGACY_ADMIN_PASSWORD = "admin123"
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


def conectar():
    """Conecta ao banco de dados SQLite e retorna conexões orientadas a dicionário/Row."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def criar_tabelas():
    """Cria todas as tabelas necessárias no banco de dados caso não existam."""
    conn = conectar()
    c = conn.cursor()

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
        senha_hash TEXT NOT NULL,
        perfil TEXT NOT NULL,      -- ADMIN, RECEPCAO, MEDICO, DENTISTA, ENFERMEIRO, PSICOLOGO
        registro_profissional TEXT, -- CRM, CRO, COREN, CRP, etc.
        cnes_unidade TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS dv_unidades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cnes TEXT NOT NULL UNIQUE,
        nome TEXT NOT NULL,
        criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """)

    patient_columns = {
        row["name"] for row in c.execute("PRAGMA table_info(pacientes)")
    }
    if "cnes_unidade" not in patient_columns:
        c.execute("ALTER TABLE pacientes ADD COLUMN cnes_unidade TEXT")

    user_columns = {row["name"] for row in c.execute("PRAGMA table_info(usuarios)")}
    if "cnes_unidade" not in user_columns:
        c.execute("ALTER TABLE usuarios ADD COLUMN cnes_unidade TEXT")
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_usuarios_cnes_unidade ON usuarios(cnes_unidade)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_pacientes_cnes_unidade ON pacientes(cnes_unidade)"
    )

    conn.commit()
    conn.close()


# ===================================================================
# GESTÃO DE USUÁRIOS E AUTENTICAÇÃO
# ===================================================================

def criar_usuario_admin_inicial():
    """Create the bootstrap administrator only when the user table is empty."""
    conn = conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM usuarios")
        if cursor.fetchone()[0] != 0:
            admin = cursor.execute(
                "SELECT senha_hash FROM usuarios WHERE usuario = ?", ("admin",)
            ).fetchone()
            if admin and check_password_hash(admin["senha_hash"], LEGACY_ADMIN_PASSWORD):
                logging.getLogger(__name__).warning(
                    "The legacy bootstrap administrator is disabled at login. "
                    "Use the recover-legacy-admin command to restore access."
                )
            conn.commit()
            return

        password = os.environ.get("INITIAL_ADMIN_PASSWORD", "")
        if len(password) < 12:
            logging.getLogger(__name__).warning(
                "No initial administrator created: set INITIAL_ADMIN_PASSWORD "
                "to a value of at least 12 characters."
            )
            conn.commit()
            return
        cursor.execute(
            """
            INSERT INTO usuarios (nome, usuario, senha_hash, perfil, registro_profissional)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("Administrador Geral", "admin", generate_password_hash(password), "ADMIN", "MASTER"),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def buscar_usuario_por_login(usuario):
    """Busca os dados de um usuário pelo seu login."""
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM usuarios WHERE usuario = ?", (usuario,))
    dado = cursor.fetchone()
    conn.close()
    return dict(dado) if dado else None


def autenticar_usuario(usuario, senha):
    """Authenticate a user and reject the historical public bootstrap password."""
    dado = buscar_usuario_por_login(usuario)
    if not dado:
        return None
    if (
        dado["usuario"] == "admin"
        and check_password_hash(dado["senha_hash"], LEGACY_ADMIN_PASSWORD)
    ):
        return None
    return dado if check_password_hash(dado["senha_hash"], senha) else None


def recover_legacy_admin_password(password):
    """Change only the existing legacy bootstrap admin's password."""
    if not isinstance(password, str) or len(password) < 12:
        raise ValueError("A nova senha deve ter pelo menos 12 caracteres.")

    conn = conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        admin = conn.execute(
            "SELECT id, senha_hash FROM usuarios WHERE usuario = ?", ("admin",)
        ).fetchone()
        if not admin or not check_password_hash(admin["senha_hash"], LEGACY_ADMIN_PASSWORD):
            conn.rollback()
            return False
        conn.execute(
            "UPDATE usuarios SET senha_hash = ? WHERE id = ?",
            (generate_password_hash(password), admin["id"]),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reset_admin_password(password):
    """Reset only the password for the existing ADMIN account named admin."""
    if not isinstance(password, str) or len(password) < 12:
        raise ValueError("A nova senha deve ter pelo menos 12 caracteres.")
    if password == LEGACY_ADMIN_PASSWORD:
        raise ValueError("A senha de bootstrap não pode ser reutilizada.")

    conn = conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        admin = conn.execute(
            "SELECT id FROM usuarios WHERE usuario = ? AND perfil = ?",
            ("admin", "ADMIN"),
        ).fetchone()
        if not admin:
            conn.rollback()
            return False
        conn.execute(
            "UPDATE usuarios SET senha_hash = ? WHERE id = ?",
            (generate_password_hash(password), admin["id"]),
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
    nome, usuario, senha, perfil, registro_profissional="", cnes_unidade=""
):
    """Cadastra um novo profissional/usuário no sistema."""
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
    if not isinstance(senha, str) or len(senha) < 12:
        raise ValueError("A senha deve ter pelo menos 12 caracteres.")
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
    if len(registro_profissional) > 80:
        raise ValueError("O registro profissional deve ter até 80 caracteres.")
    senha_hash = generate_password_hash(senha)
    conn = conectar()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO usuarios
                (nome, usuario, senha_hash, perfil, registro_profissional, cnes_unidade)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (nome, usuario, senha_hash, perfil, registro_profissional, cnes_unidade or None))
        conn.commit()
        sucesso = True
    except sqlite3.IntegrityError:
        sucesso = False  # Usuário já existente
    conn.close()
    return sucesso


def listar_usuarios():
    """Retorna a lista de todos os usuários cadastrados."""
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, nome, usuario, perfil, registro_profissional, cnes_unidade
        FROM usuarios ORDER BY nome
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
    cnes_unidade="",
    data_nascimento="",
    sexo="",
    bairro="",
    peso=0,
    altura=0,
    pressao="",
):
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

    imc, classe = calcular_imc(peso, altura)

    conn = conectar()
    try:
        conn.execute(
            """
            INSERT INTO pacientes
                (nome, telefone, cpf, cns, cnes_unidade, data_nascimento, sexo,
                 bairro, peso, altura, pressao, imc, classificacao_imc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nome,
                telefone,
                cpf,
                cns,
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
            dados.get("data_nascimento"),
            dados.get("sexo"),
            dados.get("bairro"),
            dados.get("pressao"),
            peso,
            altura,
            imc,
            classe,
            id_paciente,
        ] + parameters
        if cnes_unidade is not None:
            query += " AND cnes_unidade=?"
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
):
    conn = conectar()
    try:
        cursor = conn.execute(
            """
            INSERT INTO arquivos
                (paciente_id, tipo, nome_original, nome_stored, mime_type, descricao, criado_em)
            SELECT ?, ?, ?, ?, ?, ?, ?
            WHERE EXISTS (
                SELECT 1 FROM pacientes
                WHERE id = ? AND (? IS NULL OR cnes_unidade = ?)
            )
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


# Inicialização automática ao carregar o arquivo
criar_tabelas()
criar_usuario_admin_inicial()