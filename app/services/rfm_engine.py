"""
app/services/rfm_engine.py

RFM completo (Recência, Frequência, Valor) — Gaps Dossiê Insighta RCM,
item 4. Recência (dias desde o último atendimento) e Frequência
(visit_count) já alimentavam features separadas (InactivePatientsResponse,
patient_value_engine.compute_vip_status) — a dimensão que faltava pra
virar RFM DE VERDADE era Valor (receita histórica por paciente).

Mesmo espírito determinístico/explicável de no_show_risk_engine.py/
denial_risk_engine.py/patient_value_engine.py: nunca um modelo de
caixa-preta, sempre uma régua que o gestor consegue ler e questionar.

DECISÃO — Recência e Frequência por limiar fixo, Valor por quantil
-------------------------------------------------------------------------
Recência (dias sem voltar) e Frequência (quantas vezes já veio) têm uma
escala universal — 30 dias é "recente" e 5 visitas é "frequente" pra
qualquer clínica, mesmo raciocínio de VIP_MIN_VISITS em
patient_value_engine.py. Receita, não: uma clínica pequena de bairro e
uma rede grande têm ticket médio e volume completamente diferentes, um
limiar fixo em R$ inventaria uma régua sem sentido pra uma das duas. Por
isso o score de Valor é sempre relativo à PRÓPRIA base de pacientes do
tenant (rank percentual entre os pacientes com histórico), nunca um
corte fixo em reais.
"""
import bisect
from dataclasses import dataclass

RECENCY_SCORE_THRESHOLDS_DAYS = (30, 90, 180, 365)  # score 5,4,3,2 até o piso; acima disso, 1
FREQUENCY_SCORE_THRESHOLDS_VISITS = (10, 5, 3, 2)  # score 5,4,3,2 a partir do piso; abaixo disso, 1

RFM_SEGMENTS = (
    "campeoes",
    "fieis",
    "nao_pode_perder",
    "em_risco",
    "novos",
    "hibernando",
    "precisa_atencao",
)


def score_recency(days_since_last_appointment: int) -> int:
    for score, threshold in zip((5, 4, 3, 2), RECENCY_SCORE_THRESHOLDS_DAYS):
        if days_since_last_appointment <= threshold:
            return score
    return 1


def score_frequency(visit_count: int) -> int:
    for score, threshold in zip((5, 4, 3, 2), FREQUENCY_SCORE_THRESHOLDS_VISITS):
        if visit_count >= threshold:
            return score
    return 1


def monetary_scores(revenues: list[float]) -> list[int]:
    """Score de Valor (1-5) de cada paciente pelo RANK PERCENTUAL dele
    dentro da própria lista — maior receita, maior score. Mesma ordem de
    entrada/saída (`revenues[i]` -> retorno[i]). Lista de 1 elemento (ou
    todos empatados) cai inteira no score 3 (meio da régua) — sem base de
    comparação real, não há "topo" nem "fundo" pra apontar."""
    n = len(revenues)
    if n <= 1:
        return [3] * n
    sorted_values = sorted(revenues)
    scores = []
    for value in revenues:
        # Rank pelo MEIO do grupo de empatados (não o primeiro índice) —
        # senão uma lista inteira de valores iguais cairia toda no
        # percentil 0 (score mínimo) em vez do meio da régua.
        lo = bisect.bisect_left(sorted_values, value)
        hi = bisect.bisect_right(sorted_values, value)
        rank = (lo + hi - 1) / 2
        percentile = rank / (n - 1)
        score = 1 + int(percentile * 4.999)  # 4.999 evita que percentile==1.0 estoure pra 6
        scores.append(min(max(score, 1), 5))
    return scores


@dataclass
class RfmScore:
    recency_score: int
    frequency_score: int
    monetary_score: int
    segment: str


def classify_segment(recency_score: int, frequency_score: int, monetary_score: int) -> str:
    """
    Taxonomia RFM padrão de mercado, simplificada pra 7 segmentos
    (regras avaliadas em ordem, a primeira que bater decide — sempre
    exaustivo por causa do `else` final):

    - campeoes: recente, frequente E de alto valor — o núcleo mais
      valioso da carteira.
    - fieis: vem com frequência e recentemente, mesmo sem ainda ser
      "campeão" em valor.
    - nao_pode_perder: já gerou MUITO valor no passado, mas sumiu — maior
      urgência de reativação (mais R$ em risco por paciente).
    - em_risco: vinha com frequência e sumiu, sem necessariamente ter
      sido de alto valor — mesma urgência de reativação, ticket menor.
    - novos: recente mas ainda com pouco histórico — não deu tempo de
      provar frequência/valor ainda.
    - hibernando: baixo em tudo — pouco valor perdido em reativar,
      prioridade mais baixa da fila.
    - precisa_atencao: catch-all do meio-termo (não se encaixa em
      nenhuma régua acima) — acompanhar, sem urgência nem descarte.
    """
    if recency_score >= 4 and frequency_score >= 4 and monetary_score >= 4:
        return "campeoes"
    if recency_score >= 3 and frequency_score >= 4:
        return "fieis"
    if recency_score <= 2 and monetary_score >= 4:
        return "nao_pode_perder"
    if recency_score <= 2 and frequency_score >= 3:
        return "em_risco"
    if recency_score >= 4 and frequency_score <= 2:
        return "novos"
    if recency_score <= 2 and frequency_score <= 2 and monetary_score <= 2:
        return "hibernando"
    return "precisa_atencao"
