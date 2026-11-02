"""
sigtap.py — Catálogo de Procedimentos Odontológicos SIGTAP / SUS
"""

TABELA_SIGTAP = {
    "01.01.02.002-3": "Primeira Consulta Odontológica Programada",
    "03.07.01.002-3": "Restauração em Dente Permanente (Face Simples)",
    "03.07.01.003-1": "Restauração em Dente Permanente (Composta)",
    "03.07.02.006-1": "Exodontia (Extração) de Dente Permanente",
    "03.07.01.001-5": "Raspagem e Alisamento Coronário (Limpeza)",
    "03.07.02.003-7": "Tratamento Endodôntico (Canal)",
    "01.01.02.001-5": "Atendimento Odontológico de Urgência",
}


def listar_procedimentos():
    return [
        {"codigo": k, "nome": v} for k, v in TABELA_SIGTAP.items()
    ]