"""
app/services/denial_risk_engine.py

Implementa a Etapa 3 do pipeline descrita no briefing do produto: "IA
Preditiva de Glosas" + "Regras de Contrato" (cruzamento do valor cobrado
vs. valor acordado na tabela de repasse).

DECISÃO — Motor de regras determinístico, não um modelo de ML de caixa-preta
-------------------------------------------------------------------------
No MVP, "IA" aqui significa um conjunto de regras claras e auditáveis,
não um modelo estatístico opaco. Isso é deliberado: em RCM médico, a
diretoria da clínica PRECISA conseguir responder "por que essa consulta
foi retida?" com uma frase objetiva — é literalmente o que alimenta a
Tela B (Painel Anti-Glosa). Um classificador de ML complicaria essa
explicabilidade sem necessariamente ganhar precisão relevante nesta fase
(o volume de regras de convênio é finito e conhecido). Se o produto
evoluir para sinais mais nebulosos (padrões históricos de glosa por
convênio, por exemplo), este módulo é o lugar certo para acrescentar um
score estatístico como MAIS UMA regra na lista abaixo — sem reescrever
o motor.

DECISÃO — Strategy pattern (lista de funções-regra) em vez de um método
gigante cheio de if/else
-------------------------------------------------------------------------
Cada regra é uma função pura: recebe (appointment, contract_item, valor
cobrado) e devolve um RiskFinding ou None. Isso torna cada regra
testável isoladamente (ver tests/test_denial_risk_engine.py) e permite
adicionar uma nova regra futura (ex: "procedimento exige autorização
prévia do convênio") só acrescentando uma função à tupla _RULES, sem
tocar no restante do motor nem no BillingService.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from app.models.appointment import Appointment
from app.models.contract_item import ContractItem

# Tolerância de 1 centavo para não disparar falso positivo por
# arredondamento entre Decimal (banco) e float (payload da API).
_VALUE_TOLERANCE = Decimal("0.01")


@dataclass
class RiskFinding:
    reason_code: str
    severity: str  # "high" | "medium"
    value_saved: Decimal = Decimal("0")


@dataclass
class RiskAssessment:
    level: str  # "low" | "medium" | "high"
    reasons: list[str] = field(default_factory=list)
    value_saved_by_correction: Decimal = Decimal("0")

    @property
    def should_hold_for_review(self) -> bool:
        # Regra de negócio explícita do briefing original do produto:
        # risco "high" barra o envio automaticamente ("Alto risco de
        # glosa... barrando o envio").
        return self.level == "high"


def _rule_missing_cid(appointment: Appointment, contract_item: ContractItem | None, charged_value: Decimal) -> RiskFinding | None:
    """CID ausente é o exemplo canônico citado no briefing do produto."""
    if not appointment.cid_code:
        return RiskFinding(reason_code="missing_cid", severity="high")
    return None


def _rule_missing_procedure_code(appointment: Appointment, contract_item: ContractItem | None, charged_value: Decimal) -> RiskFinding | None:
    if not appointment.procedure_code:
        return RiskFinding(reason_code="missing_procedure_code", severity="high")
    return None


def _rule_no_contract_reference(appointment: Appointment, contract_item: ContractItem | None, charged_value: Decimal) -> RiskFinding | None:
    if contract_item is None:
        # Sem tabela de repasse cadastrada para este convênio+procedimento
        # não dá para validar o valor cobrado. Não bloqueia sozinho (é
        # falta de dado de cadastro, não erro do atendimento), mas sinaliza
        # para revisão humana com severidade média.
        return RiskFinding(reason_code="no_contract_reference", severity="medium")
    return None


def _rule_value_mismatch(appointment: Appointment, contract_item: ContractItem | None, charged_value: Decimal) -> RiskFinding | None:
    if contract_item is None:
        return None  # já coberto por _rule_no_contract_reference
    agreed = Decimal(str(contract_item.agreed_price))
    diff = charged_value - agreed
    if abs(diff) <= _VALUE_TOLERANCE:
        return None
    if diff > 0:
        # Cobrança ACIMA do valor acordado: é exatamente o padrão que um
        # convênio costuma glosar (paga só o valor de tabela). O "valor
        # salvo" reportado na Tela B é o excedente que a correção evita
        # perder no repasse.
        return RiskFinding(reason_code="value_above_contract", severity="high", value_saved=diff)
    # Cobrança ABAIXO do acordado: convênio não recusa por cobrar barato
    # demais, então NÃO é risco de glosa — é vazamento de receita, uma
    # categoria de problema diferente. Severidade média, sem "valor
    # salvo" (não foi salvo, é oportunidade de receita perdida).
    return RiskFinding(reason_code="value_below_contract_revenue_leak", severity="medium")


_RULES = (
    _rule_missing_cid,
    _rule_missing_procedure_code,
    _rule_no_contract_reference,
    _rule_value_mismatch,
)


def assess(appointment: Appointment, contract_item: ContractItem | None, charged_value: float) -> RiskAssessment:
    charged = Decimal(str(charged_value))
    findings = [result for rule in _RULES if (result := rule(appointment, contract_item, charged)) is not None]

    if not findings:
        return RiskAssessment(level="low")

    level = "high" if any(f.severity == "high" for f in findings) else "medium"
    total_saved = sum((f.value_saved for f in findings), Decimal("0"))

    return RiskAssessment(
        level=level,
        reasons=[f.reason_code for f in findings],
        value_saved_by_correction=total_saved,
    )
