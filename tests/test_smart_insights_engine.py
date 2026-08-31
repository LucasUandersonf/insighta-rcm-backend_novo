"""
tests/test_smart_insights_engine.py

Mesmo princípio de test_denial_risk_engine.py: o motor é puro (sem
banco), então testamos passando dataclasses já montados na mão, em
milissegundos, sem subir Postgres.
"""
from app.services.smart_insights_engine import DenialReasonCount, InsightsPeriodInput, generate_insights

_EMPTY_PERIOD = InsightsPeriodInput(
    denial_reason_counts=[],
    financial_hole_total=0.0,
    total_value_saved=0.0,
    avg_capacity_utilization=None,
    high_risk_no_show_count=0,
)


def test_no_insights_when_nothing_changed():
    assert generate_insights(_EMPTY_PERIOD, _EMPTY_PERIOD) == []


def test_denial_spike_above_threshold_is_flagged_critical():
    previous = InsightsPeriodInput(
        denial_reason_counts=[DenialReasonCount("Unimed Nacional", "missing_cid", 10)],
        financial_hole_total=0,
        total_value_saved=0,
        avg_capacity_utilization=None,
        high_risk_no_show_count=0,
    )
    current = InsightsPeriodInput(
        denial_reason_counts=[DenialReasonCount("Unimed Nacional", "missing_cid", 15)],  # +50%
        financial_hole_total=0,
        total_value_saved=0,
        avg_capacity_utilization=None,
        high_risk_no_show_count=0,
    )
    insights = generate_insights(current, previous)
    assert len(insights) == 1
    assert insights[0].severity == "critical"
    assert "Unimed Nacional" in insights[0].message
    assert "50%" in insights[0].message


def test_denial_spike_below_threshold_is_not_flagged():
    previous = InsightsPeriodInput(
        denial_reason_counts=[DenialReasonCount("Unimed Nacional", "missing_cid", 10)],
        financial_hole_total=0,
        total_value_saved=0,
        avg_capacity_utilization=None,
        high_risk_no_show_count=0,
    )
    current = InsightsPeriodInput(
        denial_reason_counts=[DenialReasonCount("Unimed Nacional", "missing_cid", 11)],  # +10%, abaixo do gatilho
        financial_hole_total=0,
        total_value_saved=0,
        avg_capacity_utilization=None,
        high_risk_no_show_count=0,
    )
    assert generate_insights(current, previous) == []


def test_small_sample_spike_is_ignored_as_noise():
    """2 -> 3 casos é matematicamente +50%, mas amostra baixa demais para
    significar um padrão real — mesma lógica de MIN_SAMPLE_SIZE do motor
    de risco de no-show."""
    previous = InsightsPeriodInput(
        denial_reason_counts=[DenialReasonCount("Unimed Nacional", "missing_cid", 2)],
        financial_hole_total=0,
        total_value_saved=0,
        avg_capacity_utilization=None,
        high_risk_no_show_count=0,
    )
    current = InsightsPeriodInput(
        denial_reason_counts=[DenialReasonCount("Unimed Nacional", "missing_cid", 3)],
        financial_hole_total=0,
        total_value_saved=0,
        avg_capacity_utilization=None,
        high_risk_no_show_count=0,
    )
    assert generate_insights(current, previous) == []


def test_new_reason_from_zero_needs_minimum_volume_to_be_flagged():
    current_low_volume = InsightsPeriodInput(
        denial_reason_counts=[DenialReasonCount("Bradesco Saúde", "missing_cid", 2)],
        financial_hole_total=0,
        total_value_saved=0,
        avg_capacity_utilization=None,
        high_risk_no_show_count=0,
    )
    assert generate_insights(current_low_volume, _EMPTY_PERIOD) == []

    current_high_volume = InsightsPeriodInput(
        denial_reason_counts=[DenialReasonCount("Bradesco Saúde", "missing_cid", 4)],
        financial_hole_total=0,
        total_value_saved=0,
        avg_capacity_utilization=None,
        high_risk_no_show_count=0,
    )
    insights = generate_insights(current_high_volume, _EMPTY_PERIOD)
    assert len(insights) == 1
    assert insights[0].severity == "critical"
    assert "Bradesco Saúde" in insights[0].title


def test_financial_hole_insight_carries_financial_impact_for_ranking():
    current = InsightsPeriodInput(
        denial_reason_counts=[],
        financial_hole_total=1200.50,
        total_value_saved=0,
        avg_capacity_utilization=None,
        high_risk_no_show_count=0,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    assert len(insights) == 1
    assert insights[0].severity == "warning"
    assert insights[0].financial_impact == 1200.50


def test_value_saved_improvement_is_positive_insight():
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=500.0, avg_capacity_utilization=None, high_risk_no_show_count=0
    )
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=900.0, avg_capacity_utilization=None, high_risk_no_show_count=0
    )
    insights = generate_insights(current, previous)
    assert len(insights) == 1
    assert insights[0].severity == "positive"


def test_capacity_drop_above_threshold_is_flagged():
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=0.80, high_risk_no_show_count=0
    )
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=0.65, high_risk_no_show_count=0
    )
    insights = generate_insights(current, previous)
    assert len(insights) == 1
    assert insights[0].severity == "warning"
    assert "queda" in insights[0].title.lower()


def test_high_risk_no_show_volume_uses_estimated_revenue_at_risk():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None, high_risk_no_show_count=8
    )
    insights = generate_insights(current, _EMPTY_PERIOD, estimated_no_show_revenue_at_risk=1600.0)
    assert len(insights) == 1
    assert insights[0].financial_impact == 1600.0


def test_weekday_drop_above_threshold_is_flagged_critical():
    """Reprodução direta do exemplo do redesenho: 'a agenda de
    segunda-feira caiu 10%' — aqui com 33%, acima do limiar crítico."""
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, weekday_appointment_counts={1: 12},  # segunda-feira
    )
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, weekday_appointment_counts={1: 8},  # -33%
    )
    insights = generate_insights(current, previous)
    assert len(insights) == 1
    assert insights[0].severity == "critical"
    assert "segunda-feira" in insights[0].message
    assert "33%" in insights[0].message


def test_weekday_drop_below_threshold_is_not_flagged():
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, weekday_appointment_counts={1: 12},
    )
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, weekday_appointment_counts={1: 11},  # -8%, abaixo do gatilho de 15%
    )
    assert generate_insights(current, previous) == []


def test_weekday_drop_small_sample_is_ignored_as_noise():
    """1 -> 0 é '-100%', mas com amostra abaixo de _MIN_WEEKDAY_SAMPLE não
    vira alerta — mesmo raciocínio de test_small_sample_spike_is_ignored_as_noise."""
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, weekday_appointment_counts={1: 2},
    )
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, weekday_appointment_counts={1: 0},
    )
    assert generate_insights(current, previous) == []


def test_weekday_increase_is_not_flagged_as_drop():
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, weekday_appointment_counts={1: 5},
    )
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, weekday_appointment_counts={1: 8},
    )
    assert generate_insights(current, previous) == []


def test_multiple_weekdays_can_drop_in_the_same_window():
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, weekday_appointment_counts={1: 10, 5: 10},  # segunda e sexta
    )
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, weekday_appointment_counts={1: 6, 5: 9},  # segunda -40% (crítico), sexta -10% (abaixo do gatilho)
    )
    insights = generate_insights(current, previous)
    assert len(insights) == 1
    assert "segunda-feira" in insights[0].message


def test_denial_risk_pct_above_critical_threshold():
    """Reprodução direta do exemplo do redesenho: 'risco de até 50% de
    glosas nas contas atuais'."""
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, denial_risk_pct=45.0, denial_at_risk_value=9000.0,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    assert len(insights) == 1
    assert insights[0].severity == "critical"
    assert "45%" in insights[0].message
    assert insights[0].financial_impact == 9000.0


def test_denial_risk_pct_warning_band():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, denial_risk_pct=20.0, denial_at_risk_value=1000.0,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    assert len(insights) == 1
    assert insights[0].severity == "warning"


def test_denial_risk_pct_healthy_below_threshold_is_not_flagged():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, denial_risk_pct=5.0, denial_at_risk_value=100.0,
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_denial_risk_pct_none_when_no_billing_in_period():
    """Sem faturamento no período, o percentual é None (base zero
    indefinida) — nunca deveria virar um alerta 'de graça'."""
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, denial_risk_pct=None, denial_at_risk_value=0.0,
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_insights_are_sorted_by_financial_impact_descending():
    current = InsightsPeriodInput(
        denial_reason_counts=[],
        financial_hole_total=300.0,
        total_value_saved=1000.0,
        avg_capacity_utilization=None,
        high_risk_no_show_count=10,
    )
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=200.0, avg_capacity_utilization=None, high_risk_no_show_count=0
    )
    insights = generate_insights(current, previous, estimated_no_show_revenue_at_risk=5000.0)
    impacts = [i.financial_impact for i in insights]
    assert impacts == sorted(impacts, key=lambda v: (v is None, -(v or 0)))


def _minimal(**overrides) -> InsightsPeriodInput:
    base = dict(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0,
    )
    base.update(overrides)
    return InsightsPeriodInput(**base)


def test_annual_goal_insight_absent_when_no_goal_configured():
    """Decisão explícita do usuário: sem meta manual configurada, o
    sistema NUNCA gera o insight sozinho — nunca inventa uma meta."""
    current = _minimal(annual_revenue_goal=None, elapsed_year_fraction=0.5, ytd_billed_total=100_000.0)
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_annual_goal_insight_absent_when_on_pace():
    """Metade do ano decorrida, metade da meta faturada -> exatamente no
    ritmo, sem alerta."""
    current = _minimal(annual_revenue_goal=1_000_000.0, elapsed_year_fraction=0.5, ytd_billed_total=500_000.0)
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_annual_goal_insight_absent_when_ahead_of_pace():
    current = _minimal(annual_revenue_goal=1_000_000.0, elapsed_year_fraction=0.5, ytd_billed_total=600_000.0)
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_annual_goal_insight_small_gap_is_treated_as_noise():
    """4% atrás do ritmo esperado -- abaixo do limiar de aviso, não gera alerta."""
    current = _minimal(annual_revenue_goal=1_000_000.0, elapsed_year_fraction=0.5, ytd_billed_total=480_000.0)
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_annual_goal_insight_warning_band():
    current = _minimal(annual_revenue_goal=1_000_000.0, elapsed_year_fraction=0.5, ytd_billed_total=425_000.0)  # 15% atrás
    insights = generate_insights(current, _EMPTY_PERIOD)
    assert len(insights) == 1
    assert insights[0].severity == "warning"
    assert "CRM" in insights[0].message


def test_annual_goal_insight_critical_band():
    current = _minimal(annual_revenue_goal=1_000_000.0, elapsed_year_fraction=0.5, ytd_billed_total=350_000.0)  # 30% atrás
    insights = generate_insights(current, _EMPTY_PERIOD)
    assert len(insights) == 1
    assert insights[0].severity == "critical"


def test_annual_goal_insight_mentions_inactive_patients_when_present():
    current = _minimal(
        annual_revenue_goal=1_000_000.0, elapsed_year_fraction=0.5, ytd_billed_total=350_000.0,
        inactive_patients_count=42,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    assert "42 paciente" in insights[0].message


def test_annual_goal_insight_omits_inactive_patients_note_when_zero():
    current = _minimal(
        annual_revenue_goal=1_000_000.0, elapsed_year_fraction=0.5, ytd_billed_total=350_000.0,
        inactive_patients_count=0,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    assert "paciente(s) sem consulta" not in insights[0].message
