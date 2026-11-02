"""
gerador_pdf.py — Módulo para geração de Atestados e Receituários em PDF
"""

import io
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def gerar_atestado_pdf(nome_paciente: str, dias: str, motivo: str) -> io.BytesIO:
    """Gera o arquivo PDF formatado para Atestado Odontológico."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    
    # Cabeçalho
    pdf.setFont("Helvetica-Bold", 18)
    pdf.setFillColorRGB(0.12, 0.23, 0.54)  # Azul e-SUS (#1e3a8a)
    pdf.drawString(200, 750, "ATESTADO ODONTOLÓGICO")
    
    pdf.setFont("Helvetica", 10)
    pdf.setFillColorRGB(0.4, 0.4, 0.4)
    pdf.drawString(180, 735, "Sistema DV Odonto — SUS / Saúde Bucal")
    pdf.line(50, 720, 560, 720)
    
    # Corpo do Atestado
    pdf.setFont("Helvetica", 12)
    pdf.setFillColorRGB(0, 0, 0)
    
    texto = (
        f"Atesto para os devidos fins que o(a) paciente {nome_paciente} "
        f"esteve sob meus cuidados odontológicos e necessita de {dias} dia(s) "
        f"de repouso a partir desta data."
    )
    
    text_object = pdf.beginText(50, 650)
    text_object.setFont("Helvetica", 12)
    text_object.setLeading(18)
    text_object.textLines(texto)
    pdf.drawText(text_object)
    
    if motivo:
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(50, 580, f"Observações / CID: {motivo}")
        
    # Campo para Assinatura
    pdf.line(180, 450, 430, 450)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(220, 435, "Cirurgião-Dentista Responsável")
    pdf.setFont("Helvetica", 9)
    pdf.drawString(260, 420, "CRO / Registro SUS")
    
    pdf.showPage()
    pdf.save()
    
    buffer.seek(0)
    return buffer


def gerar_receita_pdf(nome_paciente: str, medicamentos: str, orientacoes: str) -> io.BytesIO:
    """Gera o arquivo PDF formatado para Receituário Odontológico."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    
    # Cabeçalho
    pdf.setFont("Helvetica-Bold", 18)
    pdf.setFillColorRGB(0.12, 0.23, 0.54)  # Azul e-SUS (#1e3a8a)
    pdf.drawString(210, 750, "RECEITUÁRIO ODONTOLÓGICO")
    
    pdf.setFont("Helvetica", 10)
    pdf.setFillColorRGB(0.4, 0.4, 0.4)
    pdf.drawString(180, 735, "Sistema DV Odonto — Prescrição Clínica")
    pdf.line(50, 720, 560, 720)
    
    # Dados do Paciente
    pdf.setFont("Helvetica-Bold", 12)
    pdf.setFillColorRGB(0, 0, 0)
    pdf.drawString(50, 680, f"Paciente: {nome_paciente}")
    
    # Prescrição / Medicamentos
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(50, 640, "USO INTERNO / PRESCRIÇÃO:")
    
    text_object = pdf.beginText(50, 620)
    text_object.setFont("Helvetica", 11)
    text_object.setLeading(18)
    text_object.textLines(medicamentos)
    pdf.drawText(text_object)
    
    # Orientações adicionais
    if orientacoes:
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(50, 480, "ORIENTAÇÕES ADICIONAIS:")
        pdf.setFont("Helvetica", 10)
        pdf.drawString(50, 460, orientacoes)
        
    # Campo para Assinatura
    pdf.line(180, 320, 430, 320)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(220, 305, "Cirurgião-Dentista Responsável")
    pdf.setFont("Helvetica", 9)
    pdf.drawString(260, 290, "CRO / Registro SUS")
    
    pdf.showPage()
    pdf.save()
    
    buffer.seek(0)
    return buffer