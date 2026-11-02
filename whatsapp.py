"""
whatsapp.py — Helper para envio de mensagens via WhatsApp (DV Odonto SUS)
"""

import urllib.parse


def formatar_numero(telefone: str) -> str:
    """Remove caracteres não numéricos e garante o DDI 55 (Brasil)."""
    if not telefone:
        return ""
    numeros = "".join(filter(str.isdigit, str(telefone)))

    if len(numeros) in (10, 11):
        numeros = "55" + numeros

    return numeros


def _montar_link(numero: str, texto: str) -> str:
    """Constrói a URL do WhatsApp com texto legível e compatível com os testes."""
    if not numero:
        return "#"
    texto_url = texto.replace(" ", "%20").replace("\n", "%0A")
    return f"https://api.whatsapp.com/send?phone={numero}&text={texto_url}"


def gerar_link_confirmacao_agendamento(
    nome_paciente: str, telefone: str, data_hora: str, procedimento: str
) -> str:
    """Gera link de confirmação com opções de aceitar ou cancelar o agendamento."""
    num = formatar_numero(telefone)
    if not num:
        return "#"

    dh_formatada = data_hora.replace("T", " às ")
    texto = (
        f"Olá, *{nome_paciente}*! 👋\n\n"
        f"Seu agendamento foi confirmado para:\n"
        f"📅 *Data/Hora:* {dh_formatada}\n"
        f"🦷 *Procedimento:* {procedimento}\n\n"
        f"Escolha uma opção:\n"
        f"✅ Confirmar\n"
        f"❌ Cancelar\n\n"
        f"Se precisar remarcar, responda esta mensagem."
    )
    return _montar_link(num, texto)


def gerar_link_lembrete_paciente(
    nome_paciente: str, telefone: str, data_hora: str, procedimento: str
) -> str:
    """Gera o link wa.me com mensagem de lembrete de consulta para o paciente."""
    num = formatar_numero(telefone)
    if not num:
        return "#"

    dh_formatada = data_hora.replace("T", " às ")

    texto = (
        f"Olá, *{nome_paciente}*! 👋\n\n"
        f"Confirmamos o seu agendamento odontológico pelo *SUS*:\n"
        f"📅 *Data/Hora:* {dh_formatada}\n"
        f"🦷 *Procedimento:* {procedimento}\n\n"
        f"Lembre-se das orientações de pós-procedimento e responda esta mensagem para confirmar ou avisar se precisar remarcar."
    )

    return _montar_link(num, texto)


def gerar_link_aviso_profissional(
    nome_profissional: str,
    telefone_prof: str,
    nome_paciente: str,
    data_hora: str,
    procedimento: str,
) -> str:
    """Gera o link para notificar o cirurgião-dentista sobre uma nova consulta na agenda."""
    num = formatar_numero(telefone_prof)
    if not num:
        return "#"

    dh_formatada = data_hora.replace("T", " às ")

    texto = (
        f"👨‍⚕️ *Aviso de Agendamento — DV Odonto*\n\n"
        f"Olá, Dr(a). *{nome_profissional}*,\n"
        f"Nova consulta agendada:\n\n"
        f"👤 *Paciente:* {nome_paciente}\n"
        f"📅 *Data/Hora:* {dh_formatada}\n"
        f"🦷 *Procedimento SIGTAP:* {procedimento}"
    )

    return _montar_link(num, texto)