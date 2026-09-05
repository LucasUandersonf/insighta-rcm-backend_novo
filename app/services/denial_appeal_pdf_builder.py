"""
app/services/denial_appeal_pdf_builder.py

Gera o RASCUNHO do documento de Recurso de Glosa em PDF — carta formal
já com todos os dados FACTUAIS preenchidos (paciente, guia, procedimento,
convênio, valor, motivo da negativa, prazo), pronta para a clínica
revisar, completar a justificativa específica do caso e protocolar pelo
canal que a operadora usa (portal próprio, e-mail, correio — não existe
uma API padrão de submissão eletrônica de recurso entre operadoras no
Brasil, então "protocolar" continua sendo uma ação fora do sistema).

DECISÃO — gera o FORMULÁRIO/carta, não a ARGUMENTAÇÃO de mérito
-------------------------------------------------------------------------
O sistema NUNCA inventa a justificativa clínica/jurídica do caso (por
que o procedimento era necessário, por que a negativa administrativa
está errada) — isso exige julgamento humano especializado que o produto
não tem como fornecer com segurança, e um texto genérico gerado por
código correria o risco de ser usado sem revisão. O documento sai com um
parágrafo de "Justificativa do Recurso" usando o texto que o usuário
digitou na hora de gerar (ou um placeholder claro pedindo para
completar) — o ganho real aqui é eliminar o trabalho manual de montar o
CABEÇALHO/dados factuais a cada recurso (que hoje é 100% manual), não
substituir o julgamento humano sobre o mérito do caso.

Mesma biblioteca/estilo de app/services/report_pdf_builder.py
(reportlab/Platypus) — ver DECISÃO lá sobre por que reportlab em vez de
weasyprint/HTML->PDF.
"""
import io
from dataclasses import dataclass
from datetime import date, datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_APPEAL_TYPE_LABELS = {
    "tecnica": "Recurso Técnico",
    "administrativa": "Recurso Administrativo",
    "medica": "Recurso Médico (Junta Médica)",
}

_DEFAULT_JUSTIFICATION_PLACEHOLDER = (
    "[Completar aqui a justificativa específica deste caso — motivo pelo qual a glosa deve ser revertida, "
    "com base no contrato, na tabela de procedimentos vigente e/ou na documentação clínica do atendimento.]"
)


@dataclass
class DenialAppealDocumentContext:
    tenant_legal_name: str
    tenant_cnpj: str
    appeal_type: str
    operator_denial_reason: str | None
    denied_at: date
    deadline_at: date
    insurance_plan_name: str
    patient_name: str
    patient_cpf: str | None
    professional_name: str | None
    professional_registry: str | None
    procedure_code: str | None
    cid_code: str | None
    service_date: datetime
    charged_value: float
    guia_tipo: str | None
    guia_numero: str | None
    guia_senha: str | None
    justification: str | None = None


def _fmt_currency(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_date(value: date | datetime) -> str:
    return value.strftime("%d/%m/%Y")


def _row(label: str, value: str | None) -> list[str]:
    return [label, value or "Não informado"]


def build_denial_appeal_pdf(ctx: DenialAppealDocumentContext) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleCustom", parent=styles["Title"], fontSize=16)
    section_style = ParagraphStyle("SectionCustom", parent=styles["Heading2"], spaceBefore=14, spaceAfter=6)
    body_style = ParagraphStyle("BodyCustom", parent=styles["BodyText"], spaceAfter=6, leading=15)

    appeal_type_label = _APPEAL_TYPE_LABELS.get(ctx.appeal_type, ctx.appeal_type.capitalize())

    identification_rows = [
        _row("Prestador", f"{ctx.tenant_legal_name} — CNPJ {ctx.tenant_cnpj}"),
        _row("Operadora/Convênio", ctx.insurance_plan_name),
        _row("Paciente", ctx.patient_name),
        _row("CPF do paciente", ctx.patient_cpf),
        _row("Profissional executante", ctx.professional_name),
        _row("Registro profissional", ctx.professional_registry),
        _row("Data do atendimento", _fmt_date(ctx.service_date)),
        _row("Código do procedimento", ctx.procedure_code),
        _row("CID", ctx.cid_code),
        _row("Valor cobrado", _fmt_currency(ctx.charged_value)),
    ]
    if ctx.guia_numero or ctx.guia_senha or ctx.guia_tipo:
        identification_rows.extend(
            [
                _row("Tipo de guia", ctx.guia_tipo),
                _row("Número da guia", ctx.guia_numero),
                _row("Senha de autorização", ctx.guia_senha),
            ]
        )

    identification_table = Table(identification_rows, colWidths=[5 * cm, 11 * cm])
    identification_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDDDDD")),
            ]
        )
    )

    story = [
        Paragraph(appeal_type_label, title_style),
        Paragraph(f"Referente à glosa datada de {_fmt_date(ctx.denied_at)}", body_style),
        Spacer(1, 0.3 * cm),
        Paragraph("Identificação", section_style),
        identification_table,
        Paragraph("Motivo da negativa informado pela operadora", section_style),
        Paragraph(ctx.operator_denial_reason or "Não informado.", body_style),
        Paragraph("Justificativa do recurso", section_style),
        Paragraph(ctx.justification or _DEFAULT_JUSTIFICATION_PLACEHOLDER, body_style),
        Spacer(1, 0.4 * cm),
        Paragraph(
            f"Prazo de contestação junto à operadora: <b>{_fmt_date(ctx.deadline_at)}</b>.",
            body_style,
        ),
        Spacer(1, 1.2 * cm),
        Paragraph("_________________________________________", body_style),
        Paragraph("Assinatura do responsável / carimbo do prestador", body_style),
    ]

    doc.build(story)
    return buffer.getvalue()
