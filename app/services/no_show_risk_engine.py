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

CALIBRAÇÃO DOS LIMIARES (ajustável)
-------------------------------------------------------------------------
< 10% de falta histórica  -> baixo
10% a 30%                 -> médio
> 30%                     -> alto
São valores de partida razoáveis para um MVP, não uma calibração
estatística validada com dado real de clínica — o primeiro ponto a
revisar assim que houver volume de agendamentos suficiente para analisar
a distribuição real de faltas do produto.
"""
from dataclasses import dataclass
from datetime import datetime

MIN_SPECIFIC_SAMPLES = 3
_LOW_THRESHOLD = 0.10
_MEDIUM_THRESHOLD = 0.30

_COMPLETED_OR_NO_SHOW = ("completed", "no_show")


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


def _classify(rate: float) -> str:
    if rate < _LOW_THRESHOLD:
        return "baixo"
    if rate < _MEDIUM_THRESHOLD:
        return "medio"
    return "alto"


def assess(past_appointments: list, candidate_scheduled_at: datetime) -> NoShowAssessment:
    """
    `past_appointments` deve conter apenas atendimentos JÁ OCORRIDOS do
    paciente (status 'completed' ou 'no_show'), anteriores a
    candidate_scheduled_at. Cancelamentos ficam fora da amostra de
    propósito: cancelar com antecedência é um comportamento diferente de
    faltar sem avisar, e misturar os dois na mesma taxa distorceria o
    sinal — um paciente que sempre cancela educadamente não é um paciente
    de risco de falta.
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
        return NoShowAssessment(risk_level=_classify(rate), score=rate, sample_size=len(specific), used_specific_pattern=True)

    no_show_count = sum(1 for a in relevant if a.status == "no_show")
    rate = no_show_count / total
    return NoShowAssessment(risk_level=_classify(rate), score=rate, sample_size=total, used_specific_pattern=False)
