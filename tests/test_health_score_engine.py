"""tests/test_health_score_engine.py — testes puros (sem banco), mesmo
estilo de test_no_show_risk_engine.py e test_denial_risk_engine.py."""
from app.services.health_score_engine import compute_health_score


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
