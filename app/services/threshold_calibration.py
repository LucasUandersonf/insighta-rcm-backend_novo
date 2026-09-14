"""
app/services/threshold_calibration.py

Núcleo estatístico PURO compartilhado por todo motor do produto que tem
hoje um limiar "chute razoável de v1, nunca validado contra dado real"
— documentado assim em no_show_risk_engine.py, health_score_engine.py e
smart_insights_engine.py. Épico F2.1 do Plano Diretor ("Calibração por
especialidade/porte").

DECISÃO — calibrar pelo HISTÓRICO REAL da própria clínica, não por uma
tabela de benchmark "por especialidade"
-------------------------------------------------------------------------
A tentação óbvia para "calibração por especialidade" seria uma tabela
fixa tipo {"odontologia": 0.05, "psicologia": 0.25, ...}. Isso inventaria
confiança que a Insighta não tem: não existe hoje nenhum dataset real de
mercado, validado, por especialidade médica brasileira, dentro do
produto — seria uma "média de mercado" fabricada, o mesmo erro que o
Parecer Técnico criticou no benchmark de PMR antes da correção (ver
DECISÃO em app/services/analytics_service.py sobre a constante ANAHP).
Em vez disso, cada clínica é calibrada pelo PRÓPRIO histórico (mesmo
princípio já usado em no_show_risk_engine.suggest_thresholds): a mediana
e um percentil alto da distribuição real da clínica. Isso já captura o
efeito de especialidade/porte de forma honesta (uma clínica de
psiquiatria vai naturalmente ter uma mediana de falta mais alta que uma
de odontologia estética, porque SEUS PRÓPRIOS números mostram isso) sem
fingir uma autoridade externa que o produto não tem.

`Tenant.specialty` (ver migration 040) captura a especialidade como
METADADO descritivo — usado hoje só para contexto/exibição, não para
selecionar linha de uma tabela de benchmark que não existe. Quando a
Insighta tiver amostra agregada real e anonimizada suficiente por
especialidade (equivalente ao Comparativo de Clínicas já existente para
outras métricas — ver app/sql/032_network_benchmark.sql), esse campo é
o gancho natural para uma segunda camada de calibração; documentado
aqui como decisão consciente, não esquecido.
"""
import statistics
from dataclasses import dataclass


@dataclass
class PercentilePair:
    median: float
    high: float
    sample_size: int


def compute_percentile_pair(values: list[float], *, min_sample: int, high_percentile: int = 85) -> PercentilePair | None:
    """
    Mediana (P50) + um percentil alto configurável da distribuição real
    de `values` — mesmo cálculo que já existia em
    no_show_risk_engine.suggest_thresholds, extraído aqui para ser
    reaproveitado por qualquer motor com o mesmo formato de limiar
    "baixo/médio" ou "aviso/crítico" (dois pontos de corte).

    Retorna None com amostra abaixo de `min_sample` — cada chamador
    decide sua própria amostra mínima de acordo com a granularidade dos
    dados (pacientes, meses etc.): poucos pontos tornam qualquer
    percentil ruído estatístico, não um padrão real da clínica.
    """
    if len(values) < min_sample:
        return None
    sorted_values = sorted(values)
    median = statistics.median(sorted_values)
    high = compute_percentile(sorted_values, percentile=high_percentile)
    return PercentilePair(median=median, high=high, sample_size=len(sorted_values))


def compute_percentile(sorted_values: list[float], *, percentile: int) -> float:
    """P1 a P99 de uma lista JÁ ORDENADA — statistics.quantiles(n=100)
    devolve 99 pontos de corte (percentis 1 a 99); índice `percentile - 1`
    (0-indexado) corresponde ao percentil pedido. Chamador garante amostra
    mínima antes de chegar aqui (ver compute_percentile_pair /
    suggest_single_threshold)."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    return statistics.quantiles(sorted_values, n=100)[percentile - 1]


def suggest_single_threshold(values: list[float], *, min_sample: int, percentile: int = 90) -> tuple[float, int] | None:
    """
    Variante de um único ponto de corte (ex: teto/ceiling do
    health_score_engine — "acima disso já é a pior nota possível", não
    um par baixo/médio). Retorna (valor sugerido, tamanho da amostra) ou
    None abaixo de `min_sample` — mesma cautela de compute_percentile_pair.
    """
    if len(values) < min_sample:
        return None
    sorted_values = sorted(values)
    return compute_percentile(sorted_values, percentile=percentile), len(sorted_values)
