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

_WEIGHT_DENIAL = 0.45
_WEIGHT_NO_SHOW = 0.30
_WEIGHT_APPEAL = 0.25

_DENIAL_RATE_CEILING = 0.25   # 25%+ do faturamento em risco médio/alto já é a pior nota possível
_NO_SHOW_RATE_CEILING = 0.40  # 40%+ de falta já é a pior nota possível

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
) -> HealthScoreResult:
    raw: list[tuple[str, str, float, float, float]] = []  # key, label, rate, sub_score, peso-base

    if denial_risk_pct is not None:
        sub = _linear_score(denial_risk_pct, _DENIAL_RATE_CEILING)
        raw.append(("denial", "Taxa de glosa", denial_risk_pct, sub, _WEIGHT_DENIAL))

    if no_show_total > 0:
        rate = no_show_count / no_show_total
        sub = _linear_score(rate, _NO_SHOW_RATE_CEILING)
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
