"""
gerador_pdf.py — Módulo para geração de Atestados e Receituários em PDF
"""

import io
from datetime import date
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def _cabecalho(pdf, titulo: str):
    pdf.setFont("Helvetica-Bold", 18)
    pdf.setFillColorRGB(0.12, 0.23, 0.54)
    pdf.drawCentredString(306, 750, titulo)
    pdf.setFont("Helvetica", 10)
    pdf.setFillColorRGB(0.4, 0.4, 0.4)
    pdf.drawCentredString(306, 735, "Sistema DV Saúde — Odontologia / SUS")
    pdf.line(50, 725, 560, 725)
    pdf.setFillColorRGB(0, 0, 0)


def _rodape_assinatura(pdf, nome_profissional: str, cro: str):
    data_hoje = date.today().strftime("%d/%m/%Y")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(50, 200, f"Data: {data_hoje}")
    pdf.line(180, 160, 430, 160)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawCentredString(305, 148, nome_profissional or "Cirurgião-Dentista Responsável")
    pdf.setFont("Helvetica", 9)
    pdf.drawCentredString(305, 135, f"CRO: {cro}" if cro else "CRO / Registro SUS")


def gerar_atestado_pdf(
    nome_paciente: str,
    dias: str,
    motivo: str,
    nome_profissional: str = "",
    cro: str = "",
) -> io.BytesIO:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)

    _cabecalho(pdf, "ATESTADO ODONTOLÓGICO")

    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(50, 690, f"Paciente: {nome_paciente}")

    texto = (
        f"Atesto para os devidos fins que o(a) paciente {nome_paciente} "
        f"esteve sob meus cuidados odontológicos e necessita de {dias} dia(s) "
        f"de repouso a partir desta data."
    )
    t = pdf.beginText(50, 650)
    t.setFont("Helvetica", 12)
    t.setLeading(20)
    t.textLines(texto)
    pdf.drawText(t)

    if motivo:
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(50, 580, f"Observações / CID: {motivo}")

    _rodape_assinatura(pdf, nome_profissional, cro)
    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer


def gerar_receita_pdf(
    nome_paciente: str,
    medicamentos: str,
    orientacoes: str,
    nome_profissional: str = "",
    cro: str = "",
) -> io.BytesIO:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)

    _cabecalho(pdf, "RECEITUÁRIO ODONTOLÓGICO")

    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(50, 690, f"Paciente: {nome_paciente}")

    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(50, 655, "USO INTERNO / PRESCRIÇÃO:")

    t = pdf.beginText(50, 635)
    t.setFont("Helvetica", 11)
    t.setLeading(18)
    t.textLines(medicamentos)
    pdf.drawText(t)

    if orientacoes:
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(50, 480, "ORIENTAÇÕES ADICIONAIS:")
        t2 = pdf.beginText(50, 462)
        t2.setFont("Helvetica", 10)
        t2.setLeading(16)
        t2.textLines(orientacoes)
        pdf.drawText(t2)

    _rodape_assinatura(pdf, nome_profissional, cro)
    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer
