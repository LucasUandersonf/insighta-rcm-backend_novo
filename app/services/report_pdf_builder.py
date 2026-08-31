"""
app/services/report_pdf_builder.py

DECISÃO — reportlab (Platypus) em vez de weasyprint/HTML->PDF
-------------------------------------------------------------------------
weasyprint precisa de bibliotecas de sistema (Cairo/Pango) fora do
controle do requirements.txt — mais um ponto de fragilidade de deploy
(imagem Docker, Lambda layer, etc). reportlab é Python puro nas partes
que usamos aqui, mais previsível de instalar em qualquer ambiente, e o
relatório é tabular/estruturado — não precisa de layout HTML/CSS livre.
"""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.services.report_data_service import WeeklyReportData


def _fmt_currency(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_pct(value: float | None) -> str:
    return "N/D" if value is None else f"{value * 100:.1f}%"


def build_weekly_report_pdf(data: WeeklyReportData) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleCustom", parent=styles["Title"], fontSize=18)
    section_style = ParagraphStyle("SectionCustom", parent=styles["Heading2"], spaceBefore=14, spaceAfter=6)

    story = [
        Paragraph(f"Relatório semanal — {data.tenant_name}", title_style),
        Paragraph(
            f"Período: {data.period_start.strftime('%d/%m/%Y')} a {data.period_end.strftime('%d/%m/%Y')}",
            styles["Normal"],
        ),
        Spacer(1, 0.6 * cm),
    ]

    def section(title: str, rows: list[tuple[str, str]]) -> None:
        story.append(Paragraph(title, section_style))
        table = Table([[label, value] for label, value in rows], colWidths=[10 * cm, 6 * cm])
        table.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("LINEBELOW", (0, 0), (-1, -2), 0.5, colors.HexColor("#DDDDDD")),
                    ("TEXTCOLOR", (1, 0), (1, -1), colors.HexColor("#1A1A1A")),
                ]
            )
        )
        story.append(table)

    section(
        "Faturamento e prevenção de glosa",
        [
            ("Total faturado na semana", _fmt_currency(data.total_billed)),
            ("Valor salvo por correção automática", _fmt_currency(data.total_value_saved)),
            ("Faturamentos de alto risco aguardando revisão (total atual)", str(data.high_risk_pending_count)),
        ],
    )

    section(
        "Marketing e ROI",
        [
            ("Gasto com campanhas na semana", _fmt_currency(data.marketing_spend_total)),
            ("Receita atribuída a pacientes de campanha", _fmt_currency(data.marketing_revenue_attributed)),
            ("ROI estimado da semana", _fmt_pct(data.marketing_roi_pct)),
        ],
    )

    section(
        "Capacidade e agenda",
        [
            ("Utilização média de agenda (profissionais ativos)", _fmt_pct(data.avg_capacity_utilization)),
            ("Faltas (no-show) na semana", str(data.no_show_count)),
            ("Agendamentos de alto risco de falta na próxima semana", str(data.upcoming_high_risk_appointments)),
        ],
    )

    story.append(Spacer(1, 0.8 * cm))
    story.append(
        Paragraph(
            "Relatório gerado automaticamente. Valores de ROI e utilização de agenda são estimativas "
            "simplificadas — consulte o painel completo na plataforma para detalhamento por profissional e campanha.",
            ParagraphStyle("Footnote", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#666666")),
        )
    )

    doc.build(story)
    return buffer.getvalue()
