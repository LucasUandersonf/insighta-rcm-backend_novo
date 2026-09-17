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

from app.services.threshold_calibration import compute_percentile_pair

MIN_SPECIFIC_SAMPLES = 3
DEFAULT_LOW_THRESHOLD = 0.10
DEFAULT_MEDIUM_THRESHOLD = 0.30

# Achado do usuário: os defaults acima são um chute de partida, não uma
# calibração validada — MIN_PATIENTS_FOR_SUGGESTION é a amostra mínima de
# PACIENTES (não de atendimentos) antes de sugerir um limiar calculado a
# partir do histórico real da própria clínica. Mesmo raciocínio de
# "nunca inventar confiança que a evidência não dá" de MIN_SPECIFIC_SAMPLES:
# com poucos pacientes qualificados, qualquer percentil é ruído, não um
# padrão real da clínica.
MIN_PATIENTS_FOR_SUGGESTION = 10

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
class ThresholdSuggestion:
    low_threshold: float
    medium_threshold: float
    sample_size: int  # quantos pacientes qualificados entraram no cálculo


def suggest_thresholds(patient_no_show_rates: list[float]) -> ThresholdSuggestion | None:
    """
    Sugere `low_threshold`/`medium_threshold` a partir da DISTRIBUIÇÃO
    REAL de taxa de falta por paciente desta clínica (ver
    AnalyticsRepository.all_patient_no_show_rates) — não um cálculo
    genérico igual pra qualquer clínica.

    - `low_threshold` = mediana (P50): metade dos pacientes desta
      clínica, com amostra suficiente, fica abaixo disso — "comportamento
      típico" vira risco baixo.
    - `medium_threshold` = percentil 85: só os 15% piores casos da
      PRÓPRIA clínica entram na faixa de risco alto — calibrado ao
      perfil real da especialidade, não a um corte importado de outro
      lugar.

    Retorna None com menos de MIN_PATIENTS_FOR_SUGGESTION pacientes
    qualificados no histórico — mesma cautela de "indeterminado" já
    usada no resto do motor: poucos pacientes tornam qualquer percentil
    ruído estatístico, não um padrão real. Quem chama decide como
    comunicar isso (ex: "ainda não há histórico suficiente").
    """
    # Núcleo estatístico (mediana + P85) extraído para
    # threshold_calibration.compute_percentile_pair — Épico F2.1 do Plano
    # Diretor ("Calibração por especialidade/porte"), reaproveitado
    # também por smart_insights_engine (limiar de risco de glosa) e
    # health_score_engine (teto de nota). Comportamento numérico
    # idêntico ao cálculo anterior (P85 via quantiles(n=20)[16] e
    # via quantiles(n=100)[84] são o MESMO ponto, só granularidade
    # diferente de interpolação).
    pair = compute_percentile_pair(patient_no_show_rates, min_sample=MIN_PATIENTS_FOR_SUGGESTION, high_percentile=85)
    if pair is None:
        return None

    low, medium = pair.median, pair.high
    # Defesa: com uma distribuição muito concentrada, P85 pode empatar ou
    # ficar abaixo da mediana (ex: quase todo mundo com a MESMA taxa) —
    # o motor exige low < medium (mesma regra de TenantService.update_own_tenant),
    # então nunca sugerimos um par inválido.
    if medium <= low:
        medium = min(low + 0.01, 0.99)

    return ThresholdSuggestion(low_threshold=round(low, 4), medium_threshold=round(medium, 4), sample_size=pair.sample_size)


@dataclass
class NoShowAssessment:
    risk_level: str  # "indeterminado" | "baixo" | "medio" | "alto"
    score: float | None  # taxa usada para classificar; None quando indeterminado
    sample_size: int
    used_specific_pattern: bool
    # Raio-X da Receita, frente "Prevendo movimentos" — QUAL segmentação
    # de fato decidiu o risco: "dia_e_periodo" (mesmo dia da semana +
    # período, o sinal mais específico), "antecedencia" (faixa de
    # antecedência da marcação, novo nesta rodada — ver
    # _lead_time_bucket), ou "geral" (taxa histórica bruta, sem
    # segmentação). "indeterminado" quando `risk_level` também é. Mantém
    # `used_specific_pattern` (bool) para não quebrar quem já lê só ele —
    # este campo é a versão detalhada da mesma decisão.
    risk_signal: str = "geral"


def _period_of_day(dt: datetime) -> str:
    if dt.hour < 12:
        return "manha"
    if dt.hour < 18:
        return "tarde"
    return "noite"


def _weekday_pt(dt: datetime) -> int:
    # Mesma convenção usada em capacity_service.py: 0=domingo..6=sábado
    return (dt.weekday() + 1) % 7


# Raio-X da Receita, frente "Prevendo movimentos" — faixa de antecedência
# da marcação (achado da literatura de agendamento em saúde: uma consulta
# marcada com muita antecedência dá mais tempo pro plano do paciente
# mudar até lá, tipicamente mais falta do que uma marcação de última
# hora). 6/21 dias são o mesmo "chute razoável" documentado no resto do
# arquivo — nunca calibrado contra distribuição real de produção ainda.
_LEAD_TIME_SHORT_MAX_DAYS = 6  # 0-6 dias = "curto prazo"
_LEAD_TIME_LONG_MIN_DAYS = 21  # 21+ dias = "longo prazo" (o meio, 7-20, é "médio prazo")


def _lead_time_bucket(days: int) -> str:
    if days <= _LEAD_TIME_SHORT_MAX_DAYS:
        return "curto"
    if days >= _LEAD_TIME_LONG_MIN_DAYS:
        return "longo"
    return "medio"


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
    candidate_lead_time_days: int | None = None,
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

    `candidate_lead_time_days` (Raio-X da Receita, frente "Prevendo
    movimentos"): dias entre AGORA e `candidate_scheduled_at`, já
    calculado por quem chama (mesmo padrão de `has_duplicate_billing` em
    denial_risk_engine.py — a função continua pura, sem ler o relógio).
    None (default) desliga este sinal inteiro, preservando o
    comportamento de sempre para todo chamador/teste que ainda não passa
    esse dado. Quando presente, é a SEGUNDA prioridade de segmentação —
    depois de dia-da-semana+período (o sinal mais específico já
    existente), antes da taxa geral: uma clínica pode ter um padrão de
    falta forte por antecedência (ex: marcação de última hora falta
    menos) mesmo sem amostra suficiente na combinação exata de
    dia+período.
    """
    relevant = [a for a in past_appointments if a.status in _COMPLETED_OR_NO_SHOW]
    total = len(relevant)

    if total == 0:
        return NoShowAssessment(
            risk_level="indeterminado", score=None, sample_size=0, used_specific_pattern=False, risk_signal="indeterminado"
        )

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
            risk_signal="dia_e_periodo",
        )

    if candidate_lead_time_days is not None:
        target_bucket = _lead_time_bucket(candidate_lead_time_days)
        by_lead_time = [a for a in relevant if _lead_time_bucket((a.scheduled_at - a.created_at).days) == target_bucket]
        if len(by_lead_time) >= MIN_SPECIFIC_SAMPLES:
            no_show_count = sum(1 for a in by_lead_time if a.status == "no_show")
            rate = no_show_count / len(by_lead_time)
            return NoShowAssessment(
                risk_level=_classify(rate, low_threshold, medium_threshold),
                score=rate,
                sample_size=len(by_lead_time),
                used_specific_pattern=True,
                risk_signal="antecedencia",
            )

    no_show_count = sum(1 for a in relevant if a.status == "no_show")
    rate = no_show_count / total
    return NoShowAssessment(
        risk_level=_classify(rate, low_threshold, medium_threshold),
        score=rate,
        sample_size=total,
        used_specific_pattern=False,
        risk_signal="geral",
    )
