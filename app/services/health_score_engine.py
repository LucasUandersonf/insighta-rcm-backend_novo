"""
app/services/health_score_engine.py

Nota de Saúde Financeira: combina indicadores que já existem no produto
(taxa de glosa, taxa de falta, sucesso em recurso) num único score 0-100,
acompanhável mês a mês — mesmo espírito de denial_risk_engine.py e
no_show_risk_engine.py: regras determinísticas e explicáveis, nunca um
modelo de caixa-preta. O gestor precisa conseguir responder "por que
minha nota é essa" com uma frase objetiva — por isso o resultado inclui
cada componente separado, não só o número final.

DECISÃO — pesos fixos e réguas lineares, não uma calibração estatística
-------------------------------------------------------------------------
Os limiares abaixo (25% de glosa = pior nota possível, 40% de falta =
pior nota possível) são um "chute" razoável de v1, mesmo raciocínio já
documentado nos limiares de no_show_risk_engine.py — nunca uma
calibração validada contra dado real de produção ainda. Documentado
aqui, não escondido atrás de "a IA decidiu".

DECISÃO — componente ausente é EXCLUÍDO, nunca vira zero
-------------------------------------------------------------------------
Mesmo princípio de "None sobre zero" usado no resto do produto (ver
DEFAULT_APPEAL_DEADLINE_DAYS, no_show_risk_engine.py "indeterminado"):
uma clínica que ainda não teve nenhum recurso de glosa no período não
tem uma taxa de sucesso "ruim" — tem uma taxa DESCONHECIDA. O peso desse
componente é redistribuído entre os que têm amostra, proporcionalmente.
Se nenhum componente tem amostra (tenant novo, sem dado ainda), o score
inteiro é None — não existe "nota zero por falta de uso".
"""
from dataclasses import dataclass, field

from app.services.threshold_calibration import suggest_single_threshold

_WEIGHT_DENIAL = 0.45
_WEIGHT_NO_SHOW = 0.30
_WEIGHT_APPEAL = 0.25

_DENIAL_RATE_CEILING = 0.25   # 25%+ do faturamento em risco médio/alto já é a pior nota possível
_NO_SHOW_RATE_CEILING = 0.40  # 40%+ de falta já é a pior nota possível

# Amostra mínima de MESES de histórico antes de sugerir um teto calibrado
# pela própria clínica — ver DECISÃO completa em threshold_calibration.py
# (Épico F2.1 do Plano Diretor). Mesmo valor de
# smart_insights_engine.MIN_MONTHS_FOR_DENIAL_RISK_SUGGESTION (mesma
# unidade: mês, não paciente).
MIN_MONTHS_FOR_CEILING_SUGGESTION = 6
# Teto sugerido = P90 do histórico da própria clínica: "pior que 90% dos
# SEUS PRÓPRIOS meses já é a pior nota possível" — mais folgado que o P85
# usado nos limiares de aviso/crítico porque um teto de nota é um
# extremo, não um corte intermediário.
_CEILING_SUGGESTION_PERCENTILE = 90


def resolve_health_score_ceilings(tenant) -> tuple[float, float]:
    """Duck-typed (mesmo padrão de no_show_risk_engine.resolve_thresholds
    e smart_insights_engine.resolve_denial_risk_thresholds): aceita
    qualquer objeto com `health_score_denial_ceiling`/
    `health_score_no_show_ceiling` (Decimal/float/None) ou `None`."""
    if tenant is None:
        return _DENIAL_RATE_CEILING, _NO_SHOW_RATE_CEILING
    denial_ceiling = (
        float(tenant.health_score_denial_ceiling)
        if tenant.health_score_denial_ceiling is not None
        else _DENIAL_RATE_CEILING
    )
    no_show_ceiling = (
        float(tenant.health_score_no_show_ceiling)
        if tenant.health_score_no_show_ceiling is not None
        else _NO_SHOW_RATE_CEILING
    )
    return denial_ceiling, no_show_ceiling


def suggest_denial_rate_ceiling(monthly_denial_rates: list[float]) -> tuple[float, int] | None:
    """Sugere `health_score_denial_ceiling` a partir do histórico real
    (fração 0-1, mesma escala de `denial_risk_pct` já convertida pelo
    chamador) — ver DECISÃO no topo do módulo. None sem amostra
    suficiente."""
    return suggest_single_threshold(
        monthly_denial_rates, min_sample=MIN_MONTHS_FOR_CEILING_SUGGESTION, percentile=_CEILING_SUGGESTION_PERCENTILE
    )


def suggest_no_show_rate_ceiling(monthly_no_show_rates: list[float]) -> tuple[float, int] | None:
    """Mesmo raciocínio de suggest_denial_rate_ceiling, para
    `health_score_no_show_ceiling`."""
    return suggest_single_threshold(
        monthly_no_show_rates, min_sample=MIN_MONTHS_FOR_CEILING_SUGGESTION, percentile=_CEILING_SUGGESTION_PERCENTILE
    )

# Amostra mínima antes de considerar o componente de recurso de glosa —
# mesmo raciocínio de MIN_SAMPLE_SIZE em no_show_risk_engine.py: 1
# recurso decidido "ganho" viraria 100% de sucesso, o que é
# estatisticamente vazio.
_MIN_APPEAL_SAMPLE = 3


@dataclass
class HealthScoreComponent:
    key: str
    label: str
    rate: float  # 0.0-1.0 (taxa bruta, para exibir "9,2%" na tela)
    sub_score: float  # 0-100, já na escala do score final
    weight: float  # peso EFETIVO usado (após redistribuição, se houve)


@dataclass
class HealthScoreResult:
    score: float | None  # None = nenhum componente teve amostra suficiente
    components: list[HealthScoreComponent] = field(default_factory=list)


def _linear_score(rate: float, ceiling: float) -> float:
    if rate <= 0:
        return 100.0
    if rate >= ceiling:
        return 0.0
    return 100.0 * (1 - rate / ceiling)


def compute_health_score(
    *,
    denial_risk_pct: float | None,
    no_show_count: int,
    no_show_total: int,
    appeal_deferred_count: int,
    appeal_indeferido_count: int,
    denial_rate_ceiling: float = _DENIAL_RATE_CEILING,
    no_show_rate_ceiling: float = _NO_SHOW_RATE_CEILING,
) -> HealthScoreResult:
    """
    `denial_rate_ceiling`/`no_show_rate_ceiling` (Épico F2.1 do Plano
    Diretor — "Calibração por especialidade/porte"): opcionais, default
    nos mesmos valores de sempre — quem chama (AnalyticsService.get_health_score)
    resolve o valor configurado do tenant via resolve_health_score_ceilings
    e passa aqui; sem configuração, caem nos defaults. Mesmo padrão
    não-quebrador de no_show_risk_engine.assess() e
    smart_insights_engine._denial_risk_pct_insight().
    """
    raw: list[tuple[str, str, float, float, float]] = []  # key, label, rate, sub_score, peso-base

    if denial_risk_pct is not None:
        sub = _linear_score(denial_risk_pct, denial_rate_ceiling)
        raw.append(("denial", "Taxa de glosa", denial_risk_pct, sub, _WEIGHT_DENIAL))

    if no_show_total > 0:
        rate = no_show_count / no_show_total
        sub = _linear_score(rate, no_show_rate_ceiling)
        raw.append(("no_show", "Taxa de falta", rate, sub, _WEIGHT_NO_SHOW))

    appeal_total = appeal_deferred_count + appeal_indeferido_count
    if appeal_total >= _MIN_APPEAL_SAMPLE:
        rate = appeal_deferred_count / appeal_total
        raw.append(("appeal", "Sucesso em recurso de glosa", rate, rate * 100.0, _WEIGHT_APPEAL))

    if not raw:
        return HealthScoreResult(score=None, components=[])

    total_weight = sum(w for *_rest, w in raw)
    score = sum(s * w for _k, _l, _r, s, w in raw) / total_weight

    components = [
        HealthScoreComponent(key=k, label=l, rate=round(r, 4), sub_score=round(s, 1), weight=round(w / total_weight, 3))
        for k, l, r, s, w in raw
    ]
    return HealthScoreResult(score=round(score, 1), components=components)
