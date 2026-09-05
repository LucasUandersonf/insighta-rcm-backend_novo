"""
app/services/risk_alert_pdf_builder.py

Gera o PDF curto do "Alerta diário de risco de falta" — a lista NOMINAL
dos agendamentos de risco ALTO nas próximas 24h, para a clínica ligar e
confirmar antes que a falta aconteça. Ver DECISÃO completa sobre o
mecanismo de envio em app/services/report_send_service.py
(send_daily_risk_alert).

DECISÃO — documento curto e de ação, não um relatório
-------------------------------------------------------------------------
Diferente do relatório semanal (report_pdf_builder.py), que é um
retrato analítico da semana, este é uma lista de tarefas do dia: quanto
mais direto (nome, horário, telefone se houver), mais rápido alguém da
recepção consegue agir. Mesma biblioteca (reportlab/Platypus) por
consistência e pelas mesmas razões de deploy documentadas lá.
"""
import io
from dataclasses import dataclass
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


@dataclass
class RiskAlertAppointment:
    patient_full_name: str
    scheduled_at: datetime
    risk_level: str


def _fmt_datetime(value: datetime) -> str:
    return value.strftime("%d/%m %H:%M")


_RISK_LABELS = {"alto": "Alto", "medio": "Médio"}


def build_daily_risk_alert_pdf(tenant_name: str, appointments: list[RiskAlertAppointment]) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleCustom", parent=styles["Title"], fontSize=16)
    body_style = ParagraphStyle("BodyCustom", parent=styles["BodyText"], spaceAfter=6, leading=15)

    header_row = ["Paciente", "Horário", "Risco"]
    rows = [header_row] + [
        [a.patient_full_name, _fmt_datetime(a.scheduled_at), _RISK_LABELS.get(a.risk_level, a.risk_level)]
        for a in appointments
    ]
    table = Table(rows, colWidths=[9 * cm, 4 * cm, 3 * cm])
    table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEEEEE")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("LINEBELOW", (0, 0), (-1, -2), 0.5, colors.HexColor("#DDDDDD")),
            ]
        )
    )

    story = [
        Paragraph(f"Alerta de risco de falta — {tenant_name}", title_style),
        Paragraph(
            f"{len(appointments)} agendamento(s) de risco alto nas próximas 24h. "
            "Recomenda-se confirmar por telefone antes do horário.",
            body_style,
        ),
        Spacer(1, 0.4 * cm),
        table,
    ]

    doc.build(story)
    return buffer.getvalue()
