"""
calculadora.py
Módulo simples para cálculo de orçamentos odontológicos.
"""

# Tabela base de valores padrão para procedimentos
TABELA_PROCEDIMENTOS = {
    "restauracao": 150.00,
    "limpeza": 120.00,
    "canal": 450.00,
    "extracao": 200.00,
    "clareamento": 600.00
}

def calcular_orcamento(itens, desconto_percentual=0):
    """
    Recebe uma lista de nomes de procedimentos e calcula o valor total.
    """
    subtotal = sum(TABELA_PROCEDIMENTOS.get(item, 0) for item in itens)
    desconto = subtotal * (desconto_percentual / 100)
    total = subtotal - desconto
    
    return {
        "subtotal": round(subtotal, 2),
        "desconto": round(desconto, 2),
        "total": round(total, 2)
    }