"""tests/test_health_score_engine.py — testes puros (sem banco), mesmo
estilo de test_no_show_risk_engine.py e test_denial_risk_engine.py."""
from app.services.health_score_engine import (
    _DENIAL_RATE_CEILING,
    _NO_SHOW_RATE_CEILING,
    compute_health_score,
    resolve_health_score_ceilings,
    suggest_denial_rate_ceiling,
    suggest_no_show_rate_ceiling,
)


def test_todos_componentes_perfeitos_da_nota_100():
    result = compute_health_score(
        denial_risk_pct=0.0,
        no_show_count=0,
        no_show_total=50,
        appeal_deferred_count=10,
        appeal_indeferido_count=0,
    )
    assert result.score == 100.0
    assert len(result.components) == 3


def test_todos_componentes_no_teto_da_nota_0():
    result = compute_health_score(
        denial_risk_pct=0.25,  # teto de glosa
        no_show_count=20,
        no_show_total=50,  # 40% = teto de falta
        appeal_deferred_count=0,
        appeal_indeferido_count=10,  # 0% de sucesso
    )
    assert result.score == 0.0


def test_acima_do_teto_nao_fica_negativo():
    result = compute_health_score(
        denial_risk_pct=0.9,  # bem acima do teto de 25%
        no_show_count=0,
        no_show_total=0,
        appeal_deferred_count=0,
        appeal_indeferido_count=0,
    )
    assert result.score == 0.0


def test_sem_recurso_de_glosa_componente_e_excluido_nao_vira_zero():
    # Sem amostra de recurso (0 decididos) — o componente "appeal" some,
    # o peso dele é redistribuído entre os outros dois, não conta contra.
    com_appeal = compute_health_score(
        denial_risk_pct=0.10, no_show_count=5, no_show_total=50,
        appeal_deferred_count=5, appeal_indeferido_count=0,
    )
    sem_appeal = compute_health_score(
        denial_risk_pct=0.10, no_show_count=5, no_show_total=50,
        appeal_deferred_count=0, appeal_indeferido_count=0,
    )
    assert len(sem_appeal.components) == 2
    assert {c.key for c in sem_appeal.components} == {"denial", "no_show"}
    # Com sucesso 100% no appeal, incluir o componente só pode empatar ou
    # subir a nota — nunca cai por ter mais dado bom disponível.
    assert com_appeal.score is not None and sem_appeal.score is not None
    assert com_appeal.score >= sem_appeal.score


def test_amostra_de_recurso_abaixo_do_minimo_nao_conta():
    # 2 decididos é menor que _MIN_APPEAL_SAMPLE (3) — não deveria contar,
    # mesmo comportamento de "sem recurso nenhum".
    result = compute_health_score(
        denial_risk_pct=0.10, no_show_count=5, no_show_total=50,
        appeal_deferred_count=2, appeal_indeferido_count=0,
    )
    assert "appeal" not in {c.key for c in result.components}


def test_tenant_sem_nenhum_dado_devolve_none_nao_zero():
    result = compute_health_score(
        denial_risk_pct=None,
        no_show_count=0,
        no_show_total=0,
        appeal_deferred_count=0,
        appeal_indeferido_count=0,
    )
    assert result.score is None
    assert result.components == []


def test_pesos_dos_componentes_presentes_somam_1():
    result = compute_health_score(
        denial_risk_pct=0.10, no_show_count=5, no_show_total=50,
        appeal_deferred_count=0, appeal_indeferido_count=0,  # appeal exclu­ído
    )
    assert abs(sum(c.weight for c in result.components) - 1.0) < 1e-9


# ---------------------------------------------------------------------
# Épico F2.1 do Plano Diretor ("Calibração por especialidade/porte") —
# tetos configuráveis por tenant.
# ---------------------------------------------------------------------


def test_custom_ceilings_change_the_score():
    # denial_risk_pct=0.30 está ACIMA do teto default (0.25) -> nota 0
    # nesse componente. Com um teto customizado mais folgado, o mesmo
    # dado gera uma nota positiva.
    default_result = compute_health_score(
        denial_risk_pct=0.30, no_show_count=0, no_show_total=0,
        appeal_deferred_count=0, appeal_indeferido_count=0,
    )
    assert default_result.score == 0.0

    custom_result = compute_health_score(
        denial_risk_pct=0.30, no_show_count=0, no_show_total=0,
        appeal_deferred_count=0, appeal_indeferido_count=0,
        denial_rate_ceiling=0.60,
    )
    assert custom_result.score is not None and custom_result.score > 0.0


def test_resolve_health_score_ceilings_none_tenant_uses_defaults():
    assert resolve_health_score_ceilings(None) == (_DENIAL_RATE_CEILING, _NO_SHOW_RATE_CEILING)


class _FakeTenant:
    def __init__(self, denial_ceiling=None, no_show_ceiling=None):
        self.health_score_denial_ceiling = denial_ceiling
        self.health_score_no_show_ceiling = no_show_ceiling


def test_resolve_health_score_ceilings_tenant_without_config_uses_defaults():
    assert resolve_health_score_ceilings(_FakeTenant()) == (_DENIAL_RATE_CEILING, _NO_SHOW_RATE_CEILING)


def test_resolve_health_score_ceilings_tenant_with_config():
    assert resolve_health_score_ceilings(_FakeTenant(denial_ceiling=0.5, no_show_ceiling=0.6)) == (0.5, 0.6)


def test_suggest_denial_rate_ceiling_below_min_sample_returns_none():
    assert suggest_denial_rate_ceiling([0.1, 0.2, 0.3]) is None


def test_suggest_denial_rate_ceiling_with_enough_months():
    monthly_rates = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
    result = suggest_denial_rate_ceiling(monthly_rates)
    assert result is not None
    value, sample_size = result
    assert sample_size == 6
    assert value >= 0.25  # P90 deveria ficar perto do topo da distribuição


def test_suggest_no_show_rate_ceiling_below_min_sample_returns_none():
    assert suggest_no_show_rate_ceiling([0.1, 0.2, 0.3]) is None


def test_suggest_no_show_rate_ceiling_with_enough_months():
    monthly_rates = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
    result = suggest_no_show_rate_ceiling(monthly_rates)
    assert result is not None
    value, sample_size = result
    assert sample_size == 6
    assert value >= 0.25
