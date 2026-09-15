"""tests/test_health_score_engine.py — testes puros (sem banco), mesmo
estilo de test_no_show_risk_engine.py e test_denial_risk_engine.py."""
from app.services.health_score_engine import (
    _DENIAL_RATE_CEILING,
    _NO_SHOW_RATE_CEILING,
    compute_health_score,
    resolve_health_score_ceilings,
    resolve_no_show_ceiling_for_period,
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


# ---------------------------------------------------------------------
# "Junta Técnica Insighta" — calibração do teto de falta por
# especialidade DENTRO do mesmo tenant (nunca uma tabela de benchmark
# externa por especialidade médica).
# ---------------------------------------------------------------------


def test_single_specialty_period_keeps_the_flat_default_ceiling():
    # Só 1 especialidade contribuiu no período -> não há "mistura" pra
    # calibrar, o teto continua o de sempre (comportamento inalterado
    # pra maioria das clínicas, de especialidade única).
    ceiling = resolve_no_show_ceiling_for_period(
        None,
        counts_by_specialty={"Urologia": (10, 40)},
        monthly_rates_by_specialty={"Urologia": [0.05] * 6},
    )
    assert ceiling == _NO_SHOW_RATE_CEILING


def test_no_specialty_data_keeps_the_flat_default_ceiling():
    ceiling = resolve_no_show_ceiling_for_period(None, counts_by_specialty={}, monthly_rates_by_specialty={})
    assert ceiling == _NO_SHOW_RATE_CEILING


def test_explicit_tenant_ceiling_always_wins_over_specialty_mix():
    # Mesmo com 2+ especialidades no período, um teto configurado
    # manualmente pelo gestor nunca é sobrescrito pela calibração
    # automática (mesmo princípio de "escolha explícita sempre vence"
    # do resto do produto).
    tenant = _FakeTenant(no_show_ceiling=0.55)
    ceiling = resolve_no_show_ceiling_for_period(
        tenant,
        counts_by_specialty={"Urologia": (20, 80), "Pneumologia": (5, 100)},
        monthly_rates_by_specialty={"Urologia": [0.25] * 6, "Pneumologia": [0.10] * 6},
    )
    assert ceiling == 0.55


def test_multi_specialty_period_blends_each_specialtys_own_ceiling_weighted_by_volume():
    # Urologia (25% de falta histórica, P90 puxa pra ~0.25) domina o
    # volume do período (80 de 100 atendimentos) -> o teto ponderado
    # deveria ficar bem mais perto do teto da Urologia do que do teto,
    # bem mais baixo, da Pneumologia (10%).
    ceiling = resolve_no_show_ceiling_for_period(
        None,
        counts_by_specialty={"Urologia": (20, 80), "Pneumologia": (5, 20)},
        monthly_rates_by_specialty={
            "Urologia": [0.20, 0.22, 0.24, 0.25, 0.26, 0.28],
            "Pneumologia": [0.08, 0.09, 0.10, 0.11, 0.12, 0.13],
        },
    )
    assert ceiling is not None
    # Entre os dois tetos individuais, mas puxado pro lado de maior volume.
    assert 0.20 < ceiling < 0.26
    # E mais perto do teto de Urologia (maior peso) do que da média simples.
    simple_average = (0.28 + 0.13) / 2  # aproximação grosseira dos P90 de cada série
    assert abs(ceiling - 0.28) < abs(ceiling - simple_average)


def test_specialty_without_enough_own_history_falls_back_to_default_ceiling_for_its_share():
    # Pneumologia tem volume no período mas SEM histórico mensal
    # suficiente (menos de MIN_MONTHS_FOR_CEILING_SUGGESTION meses) ->
    # essa fatia usa o default de 40%, nunca inventa uma confiança que a
    # amostra não sustenta.
    ceiling = resolve_no_show_ceiling_for_period(
        None,
        counts_by_specialty={"Urologia": (20, 50), "Pneumologia": (5, 50)},
        monthly_rates_by_specialty={"Urologia": [0.20] * 6, "Pneumologia": [0.05, 0.06]},
    )
    assert ceiling is not None
    expected = (0.20 * 50 + _NO_SHOW_RATE_CEILING * 50) / 100
    assert abs(ceiling - expected) < 1e-9
