from whatsapp import (
    formatar_numero,
    gerar_link_confirmacao_agendamento,
    gerar_link_lembrete_paciente,
)


def test_formatar_numero_adds_country_code():
    assert formatar_numero("11999887766") == "5511999887766"


def test_confirmacao_link_contains_options():
    link = gerar_link_confirmacao_agendamento(
        "Ana Silva",
        "11999887766",
        "2026-08-10T14:00",
        "Limpeza",
    )
    assert "api.whatsapp.com/send" in link
    assert "Confirmar" in link
    assert "Cancelar" in link


def test_lembrete_link_contains_procedure_and_instructions():
    link = gerar_link_lembrete_paciente(
        "Ana Silva",
        "11999887766",
        "2026-08-10T14:00",
        "Limpeza",
    )
    assert "api.whatsapp.com/send" in link
    assert "Limpeza" in link
    assert "pós-procedimento" in link.lower()
