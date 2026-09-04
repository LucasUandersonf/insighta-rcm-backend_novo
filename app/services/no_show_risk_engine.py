"""
app/services/no_show_risk_engine.py

Fase 1 do "Alerta de Risco de Falta": calcula, a partir do HISTÓRICO
PASSADO do próprio paciente, o risco de ele faltar a um novo
agendamento. Puramente baseado em dado que já existe no banco — nenhuma
tabela nova, nenhuma integração nova. A fila de espera + convite
automático (Fase 2, discutida e propositalmente adiada) ficaria num
módulo separado, construída em cima do que este arquivo calcula.

DECISÃO — dois sinais, não um: taxa GERAL de falta e taxa ESPECÍFICA por
dia da semana + período do dia
-------------------------------------------------------------------------
Um paciente pode ter histórico geral bom mas faltar sistematicamente numa
combinação específica (ex: segunda de tarde, por um conflito recorrente
de agenda pessoal) — é exatamente o exemplo que motivou a feature.
Calculamos os dois sinais: a taxa geral (mais amostras, estatisticamente
mais estável) e a taxa específica do mesmo weekday+período do
agendamento sendo criado (mais precisa quando há amostra suficiente).
Só usamos a específica quando ela tem pelo menos MIN_SPECIFIC_SAMPLES
ocorrências — do contrário, "faltou 1 de 1 vez numa segunda de tarde"
viraria "100% de risco", o que é estatisticamente vazio e assustaria o
produto sem necessidade.

DECISÃO — "indeterminado" é um nível de risco explícito, não "baixo"
-------------------------------------------------------------------------
Um paciente novo, sem nenhum atendimento passado, não tem risco BAIXO —
tem risco DESCONHECIDO. Tratar ausência de dado como "baixo risco" seria
uma afirmação de confiança que os dados não sustentam — o mesmo cuidado
que já tomamos no motor de risco de glosa (nunca inventar confiança que
a evidência não dá).

CALIBRAÇÃO DOS LIMIARES — agora configurável por tenant
-------------------------------------------------------------------------
Valores de PARTIDA (usados quando o tenant não configurou nada em "Minha
Clínica" — ver Tenant.no_show_low_threshold/no_show_medium_threshold):

< 10% de falta histórica  -> baixo
10% a 30%                 -> médio
> 30%                     -> alto

Estes defaults nunca foram uma calibração estatística validada com dado
real — eram um "chute" razoável de MVP. Cada clínica tem um perfil de
falta muito diferente por especialidade (odontologia estética costuma
faltar bem menos que psicologia/psiquiatria, por exemplo), então
`assess()` aceita `low_threshold`/`medium_threshold` como parâmetros
OPCIONAIS — quem chama (appointment_service.py,
normalization_service.py) busca o valor configurado do tenant e passa
aqui; sem configuração, caem nos defaults acima. A função continua pura
(sem tocar banco), só ganhou dois parâmetros com default.
"""
from dataclasses import dataclass
from datetime import datetime

MIN_SPECIFIC_SAMPLES = 3
DEFAULT_LOW_THRESHOLD = 0.10
DEFAULT_MEDIUM_THRESHOLD = 0.30

_COMPLETED_OR_NO_SHOW = ("completed", "no_show")


def resolve_thresholds(tenant) -> tuple[float, float]:
    """Duck-typed de propósito (não importa app.models.tenant.Tenant —
    este módulo nunca toca banco/ORM): aceita qualquer objeto com os
    atributos `no_show_low_threshold`/`no_show_medium_threshold`
    (Decimal/float/None), OU `None` (tenant não encontrado — não deveria
    acontecer para um tenant_id de JWT válido, mas defensivo é melhor que
    AttributeError). Centraliza a regra "None -> default do módulo" para
    os dois chamadores (appointment_service.py, normalization_service.py)
    nunca divergirem nessa conversão."""
    if tenant is None:
        return DEFAULT_LOW_THRESHOLD, DEFAULT_MEDIUM_THRESHOLD
    low = float(tenant.no_show_low_threshold) if tenant.no_show_low_threshold is not None else DEFAULT_LOW_THRESHOLD
    medium = (
        float(tenant.no_show_medium_threshold) if tenant.no_show_medium_threshold is not None else DEFAULT_MEDIUM_THRESHOLD
    )
    return low, medium


@dataclass
class NoShowAssessment:
    risk_level: str  # "indeterminado" | "baixo" | "medio" | "alto"
    score: float | None  # taxa usada para classificar; None quando indeterminado
    sample_size: int
    used_specific_pattern: bool


def _period_of_day(dt: datetime) -> str:
    if dt.hour < 12:
        return "manha"
    if dt.hour < 18:
        return "tarde"
    return "noite"


def _weekday_pt(dt: datetime) -> int:
    # Mesma convenção usada em capacity_service.py: 0=domingo..6=sábado
    return (dt.weekday() + 1) % 7


def _classify(rate: float, low_threshold: float, medium_threshold: float) -> str:
    if rate < low_threshold:
        return "baixo"
    if rate < medium_threshold:
        return "medio"
    return "alto"


def assess(
    past_appointments: list,
    candidate_scheduled_at: datetime,
    *,
    low_threshold: float = DEFAULT_LOW_THRESHOLD,
    medium_threshold: float = DEFAULT_MEDIUM_THRESHOLD,
) -> NoShowAssessment:
    """
    `past_appointments` deve conter apenas atendimentos JÁ OCORRIDOS do
    paciente (status 'completed' ou 'no_show'), anteriores a
    candidate_scheduled_at. Cancelamentos ficam fora da amostra de
    propósito: cancelar com antecedência é um comportamento diferente de
    faltar sem avisar, e misturar os dois na mesma taxa distorceria o
    sinal — um paciente que sempre cancela educadamente não é um paciente
    de risco de falta.

    `low_threshold`/`medium_threshold` — ver DECISÃO "agora configurável
    por tenant" no topo do módulo. Quem chama busca o valor configurado
    em Tenant (None = usar o default do módulo) antes de invocar esta
    função; ela mesma nunca toca banco.
    """
    relevant = [a for a in past_appointments if a.status in _COMPLETED_OR_NO_SHOW]
    total = len(relevant)

    if total == 0:
        return NoShowAssessment(risk_level="indeterminado", score=None, sample_size=0, used_specific_pattern=False)

    target_weekday = _weekday_pt(candidate_scheduled_at)
    target_period = _period_of_day(candidate_scheduled_at)

    specific = [
        a for a in relevant
        if _weekday_pt(a.scheduled_at) == target_weekday and _period_of_day(a.scheduled_at) == target_period
    ]

    if len(specific) >= MIN_SPECIFIC_SAMPLES:
        no_show_count = sum(1 for a in specific if a.status == "no_show")
        rate = no_show_count / len(specific)
        return NoShowAssessment(
            risk_level=_classify(rate, low_threshold, medium_threshold),
            score=rate,
            sample_size=len(specific),
            used_specific_pattern=True,
        )

    no_show_count = sum(1 for a in relevant if a.status == "no_show")
    rate = no_show_count / total
    return NoShowAssessment(
        risk_level=_classify(rate, low_threshold, medium_threshold),
        score=rate,
        sample_size=total,
        used_specific_pattern=False,
    )
