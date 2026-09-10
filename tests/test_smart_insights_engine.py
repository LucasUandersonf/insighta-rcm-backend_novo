"""
tests/test_smart_insights_engine.py

Mesmo princípio de test_denial_risk_engine.py: o motor é puro (sem
banco), então testamos passando dataclasses já montados na mão, em
milissegundos, sem subir Postgres.
"""
import pytest

from app.services.smart_insights_engine import (
    DenialReasonCount,
    InsightsPeriodInput,
    build_network_comparativo_insight,
    describe_worst_no_show_weekday,
    generate_insights,
    is_true_denial_risk_reason,
)

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
    assert insights[0].category == "faturamento"
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


def test_is_true_denial_risk_reason_excludes_revenue_leak():
    """Ver DECISÃO em is_true_denial_risk_reason — cobrar ABAIXO do
    contrato não é motivo de recusa do convênio, é vazamento de receita
    da própria clínica."""
    assert is_true_denial_risk_reason("value_below_contract_revenue_leak") is False
    assert is_true_denial_risk_reason("missing_cid") is True
    assert is_true_denial_risk_reason("missing_procedure_code") is True
    assert is_true_denial_risk_reason("no_contract_reference") is True
    assert is_true_denial_risk_reason("value_above_contract") is True


def test_denial_spike_never_flags_a_plan_for_revenue_leak_alone():
    """Achado do usuário: "value_below_contract_revenue_leak" não é
    recusa — um convênio cobrado abaixo do contrato NUNCA deveria virar
    um card "está recusando mais pagamentos", mesmo que o volume dispare
    (esse buraco já tem card próprio, _financial_hole_insight)."""
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0,
    )
    current = InsightsPeriodInput(
        denial_reason_counts=[DenialReasonCount("Unimed Nacional", "value_below_contract_revenue_leak", 6)],
        financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None, high_risk_no_show_count=0,
    )
    assert generate_insights(current, previous) == []


def test_denial_spike_ignores_revenue_leak_but_still_flags_a_real_reason_in_the_same_plan():
    """Mesmo convênio com os dois motivos ao mesmo tempo: só o motivo de
    recusa DE VERDADE (missing_cid) deveria virar manchete/entrar na
    contagem — revenue leak fica de fora, sem contaminar "e mais N
    motivo(s)" nem a contagem total."""
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0,
    )
    current = InsightsPeriodInput(
        denial_reason_counts=[
            DenialReasonCount("Unimed Nacional", "missing_cid", 5),
            DenialReasonCount("Unimed Nacional", "value_below_contract_revenue_leak", 9),  # maior volume, mas não é recusa
        ],
        financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None, high_risk_no_show_count=0,
    )
    insights = generate_insights(current, previous)
    unimed_insights = [i for i in insights if "Unimed Nacional" in i.title]
    assert len(unimed_insights) == 1
    assert "código da doença" in unimed_insights[0].message
    assert "mais barato" not in unimed_insights[0].message
    assert "mais baixo" not in unimed_insights[0].message
    assert "mais 1 motivo" not in unimed_insights[0].message  # o único outro motivo era o filtrado


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


def test_denial_spike_consolidates_multiple_reasons_of_the_same_plan_into_one_card():
    """Achado real (dado sintético chegou a gerar 6 cards de 'Bradesco
    Saúde' ao mesmo tempo, um por motivo) — agora é 1 card por convênio,
    mesmo quando vários motivos disparam juntos."""
    current = InsightsPeriodInput(
        denial_reason_counts=[
            DenialReasonCount("Bradesco Saúde", "missing_cid", 6),
            DenialReasonCount("Bradesco Saúde", "value_above_contract", 4),
        ],
        financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None, high_risk_no_show_count=0,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    bradesco_insights = [i for i in insights if "Bradesco Saúde" in i.title]
    assert len(bradesco_insights) == 1
    # O motivo de maior volume (missing_cid, 6 casos) vira a manchete; o
    # outro motivo entra como "mais 1 motivo", nunca some silenciosamente.
    assert "código da doença" in bradesco_insights[0].message
    assert "mais 1 motivo" in bradesco_insights[0].message
    assert "10" in bradesco_insights[0].message  # total consolidado (6 + 4)


def test_denial_spike_has_action_pointing_to_the_exact_plans_high_risk_billing_queue():
    """Antes o botão sempre caía na fila GERAL ("/") — agora aponta já
    filtrado pelo convênio EXATO que disparou o card (ver DECISÃO em
    _denial_spike_insights), não a fila inteira."""
    current = InsightsPeriodInput(
        denial_reason_counts=[DenialReasonCount("Unimed Nacional", "missing_cid", 5, plan_id="plan-unimed-123")],
        financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None, high_risk_no_show_count=0,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    assert insights[0].action_label is not None
    assert "Unimed Nacional" in insights[0].action_label
    assert insights[0].action_href == "/?insurance_plan_id=plan-unimed-123"


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
    # DECISÃO — o botão aponta pra lista real das contas, não mais direto
    # pro cadastro de Contratos (ver DECISÃO em _financial_hole_insight).
    assert insights[0].action_href == "#buraco-financeiro"


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
    assert "vazios" in insights[0].title.lower()


def test_capacity_drop_uses_estimated_idle_capacity_revenue_lost():
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=0.80, high_risk_no_show_count=0
    )
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=0.65, high_risk_no_show_count=0
    )
    insights = generate_insights(current, previous, estimated_idle_capacity_revenue_lost=2400.0)
    assert len(insights) == 1
    assert insights[0].financial_impact == 2400.0
    assert "deixaram de entrar" in insights[0].message


def test_capacity_drop_without_idle_estimate_omits_financial_impact():
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=0.80, high_risk_no_show_count=0
    )
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=0.65, high_risk_no_show_count=0
    )
    insights = generate_insights(current, previous)
    assert insights[0].financial_impact is None
    assert "deixaram de entrar" not in insights[0].message


def test_capacity_drop_names_idlest_professional_when_below_free_threshold():
    """Achado do usuário: 'algum profissional específico' não é uma
    ação, é uma pergunta de volta pro gestor — agora nomeia quem está
    de fato ocioso e aponta pro botão certo (candidatos a recontato
    daquele profissional)."""
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=0.80, high_risk_no_show_count=0
    )
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=0.65, high_risk_no_show_count=0,
        professional_utilization_rates=[("prof-1", "Dr. Ricardo", 0.90), ("prof-2", "Dra. Ana", 0.40)],
    )
    insights = generate_insights(current, previous)
    assert len(insights) == 1
    assert "Dra. Ana" in insights[0].message
    assert "Dr. Ricardo" not in insights[0].message
    assert insights[0].action_label == "Ver candidatos pra agenda de Dra. Ana"
    assert insights[0].action_href == "#professional:prof-2"


def test_capacity_drop_falls_back_to_generic_text_when_nobody_is_free_enough():
    """Ninguém abaixo do piso de "agenda livre" — mesmo texto/ação
    genéricos de antes, nunca nomeia alguém que o painel de apoio não
    classificaria como ocioso."""
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=0.80, high_risk_no_show_count=0
    )
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=0.65, high_risk_no_show_count=0,
        professional_utilization_rates=[("prof-1", "Dr. Ricardo", 0.90), ("prof-2", "Dra. Ana", 0.70)],
    )
    insights = generate_insights(current, previous)
    assert "Dra. Ana" not in insights[0].message
    assert insights[0].action_label == "Ver ocupação por profissional"
    assert insights[0].action_href == "#agenda-resumo"


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
    # DECISÃO — o botão aponta pra visão focada nesse dia da semana (lista
    # de recontato), não mais só pro gráfico de volume (ver DECISÃO em
    # _weekday_drop_insight).
    assert insights[0].action_href == "#weekday:1"
    assert "segunda-feira" in insights[0].action_label.lower()


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


def test_weekday_no_show_rate_above_average_is_flagged():
    """Segunda-feira: 4 de 10 (40%) faltaram; sexta: 1 de 10 (10%) —
    média do período = 5/20 = 25%. Segunda fica 15pp acima da média
    (warning; crítico seria >=20pp) — sexta fica ABAIXO, não é alerta."""
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, weekday_no_show_counts={1: (4, 10), 5: (1, 10)},
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    assert len(insights) == 1
    assert "segunda-feira" in insights[0].message.lower()
    assert insights[0].severity == "warning"
    # DECISÃO — aponta pra visão focada nesse dia (ver DECISÃO em
    # _weekday_no_show_rate_insight), sem consulta futura marcada nesse
    # dia da semana o texto não menciona nenhum número em cima do padrão
    # histórico.
    assert insights[0].action_href == "#weekday:1"
    assert "consulta(s) marcada(s)" not in insights[0].message


def test_weekday_no_show_rate_mentions_upcoming_marked_appointments_with_risk():
    """Conecta o padrão histórico com o que já está marcado pra frente —
    achado do usuário: 'um lembrete pode ajudar' não dizia quem ligar
    nem quando."""
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, weekday_no_show_counts={1: (4, 10), 5: (1, 10)},
        upcoming_risk_count_by_weekday={1: 3, 2: 7},  # 3 é do dia flagrado (segunda); 2 (terça) não deve aparecer
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    assert len(insights) == 1
    assert "3 consulta(s) marcada(s)" in insights[0].message
    assert "7 consulta(s)" not in insights[0].message


def test_weekday_no_show_rate_far_above_average_is_critical():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, weekday_no_show_counts={1: (8, 10), 5: (1, 10)},  # segunda 80%, média 45% -> +35pp
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    assert len(insights) == 1
    assert insights[0].severity == "critical"


def test_weekday_no_show_rate_close_to_average_is_not_flagged():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, weekday_no_show_counts={1: (3, 10), 5: (2, 10)},  # 30% vs 20%, média 25% -> +5pp
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_weekday_no_show_rate_small_sample_is_ignored_as_noise():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, weekday_no_show_counts={1: (2, 2), 5: (1, 10)},  # segunda: só 2 amostras
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_weekday_drop_only_the_worst_day_becomes_a_card_when_several_qualify():
    """Achado real (mesmo espírito de test_denial_spike_consolidates_multiple_reasons_of_the_same_plan_into_one_card):
    3 dias caindo ao mesmo tempo não deveria virar 3 cards quase iguais —
    só o de MAIOR queda vira insight."""
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0,
        weekday_appointment_counts={1: 10, 3: 10, 5: 10},  # segunda, quarta, sexta
    )
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0,
        # segunda -20%, quarta -50% (a pior), sexta -30% — todas acima do gatilho de 15%
        weekday_appointment_counts={1: 8, 3: 5, 5: 7},
    )
    insights = generate_insights(current, previous)
    weekday_titles = [i for i in insights if "está com menos consultas marcadas" in i.title]
    assert len(weekday_titles) == 1
    assert "quarta-feira" in weekday_titles[0].title.lower()
    assert weekday_titles[0].category == "agenda"


def test_weekday_no_show_rate_only_the_worst_day_becomes_a_card_when_several_qualify():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0,
        # segunda 60% (6/10), quarta 44% (4/9), sexta 5% (1/20) — média
        # geral = 11/39 = 28.2%. Segunda fica +31.8pp acima (a pior,
        # crítico), quarta fica +16.2pp acima (warning), sexta fica
        # ABAIXO da média (não é candidata).
        weekday_no_show_counts={1: (6, 10), 3: (4, 9), 5: (1, 20)},
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    weekday_titles = [i for i in insights if "faltam mais" in i.title]
    assert len(weekday_titles) == 1
    assert "segunda-feira" in weekday_titles[0].title.lower()


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
    # Linguagem sem jargão (nem sigla) — ver DECISÃO de reescrita no topo
    # de smart_insights_engine.py: nunca "CRM", sempre a ação em português comum.
    assert "trazer pacientes novos" in insights[0].message or "reativar" in insights[0].message


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
    assert "não voltam há mais de um ano" not in insights[0].message


def test_annual_goal_insight_has_action_pointing_to_inactive_patients_when_present():
    """Antes o insight recomendava em texto mas não tinha botão nenhum
    — não existia lista de "quem são" os pacientes inativos ainda. Ver
    DECISÃO em AnalyticsRepository.list_inactive_patients."""
    current = _minimal(
        annual_revenue_goal=1_000_000.0, elapsed_year_fraction=0.5, ytd_billed_total=350_000.0,
        inactive_patients_count=42,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    assert insights[0].action_label == "Ver quem não voltou"
    assert insights[0].action_href == "#carteira-inativa"
    # Categoria é faturamento (meta de faturamento anual) mesmo o botão
    # apontando pra uma seção de Agenda — categoria segue o QUE o
    # insight mede, não pra onde o botão leva.
    assert insights[0].category == "faturamento"


def test_annual_goal_insight_has_no_action_when_no_inactive_patients():
    """Sem ninguém pra chamar de volta, sem botão — nunca um link pra
    uma seção que ia aparecer vazia."""
    current = _minimal(
        annual_revenue_goal=1_000_000.0, elapsed_year_fraction=0.5, ytd_billed_total=350_000.0,
        inactive_patients_count=0,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    assert insights[0].action_label is None
    assert insights[0].action_href is None


def test_professional_outlier_flagged_when_double_the_clinic_average():
    # Média da clínica 10% (denial_risk_pct=10.0, escala 0-100) — Dr. X a
    # 25% é 2.5x a média E 15 pontos percentuais acima -> passa os dois limiares.
    current = _minimal(
        denial_risk_pct=10.0,
        professional_denial_rates=[("prof-x", "Dr. X", 0.25, 8), ("prof-y", "Dr. Y", 0.09, 12)],
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    outlier = next(i for i in insights if "fora do padrão" in i.title)
    assert "Dr. X" in outlier.title
    assert outlier.severity == "warning"


def test_professional_outlier_has_action_pointing_to_the_exact_professional():
    """Antes o botão sempre caía em "/professionals" (a lista GERAL) —
    agora leva direto pro profissional que disparou o card via query
    param, pra tela rolar/realçar a linha exata (ver DECISÃO em
    _professional_outlier_insight)."""
    current = _minimal(
        denial_risk_pct=10.0,
        professional_denial_rates=[("prof-x-id", "Dr. X", 0.25, 8), ("prof-y-id", "Dr. Y", 0.09, 12)],
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    outlier = next(i for i in insights if "fora do padrão" in i.title)
    assert outlier.action_label is not None
    assert "Dr. X" in outlier.action_label
    assert outlier.action_href == "/professionals?highlight=prof-x-id"


def test_professional_outlier_absent_when_below_ratio_threshold():
    # 15% vs média 10% -> só 1.5x, abaixo do limiar de 2x.
    current = _minimal(denial_risk_pct=10.0, professional_denial_rates=[("prof-x", "Dr. X", 0.15, 8)])
    insights = generate_insights(current, _EMPTY_PERIOD)
    assert not any("fora do padrão" in i.title for i in insights)


def test_professional_outlier_absent_when_gap_is_trivial_in_absolute_terms():
    # 2% vs média 1% -> 2x em proporção, mas só 1 ponto percentual de
    # diferença absoluta — abaixo do piso de 5pp, não deveria disparar.
    current = _minimal(denial_risk_pct=1.0, professional_denial_rates=[("prof-x", "Dr. X", 0.02, 8)])
    insights = generate_insights(current, _EMPTY_PERIOD)
    assert not any("fora do padrão" in i.title for i in insights)


def test_professional_outlier_only_flags_the_worst_case():
    current = _minimal(
        denial_risk_pct=10.0,
        professional_denial_rates=[
            ("prof-x", "Dr. X", 0.25, 8),
            ("prof-z", "Dr. Z", 0.40, 6),
            ("prof-y", "Dr. Y", 0.09, 12),
        ],
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    outliers = [i for i in insights if "fora do padrão" in i.title]
    assert len(outliers) == 1
    assert "Dr. Z" in outliers[0].title


def test_professional_outlier_absent_without_denial_risk_pct():
    current = _minimal(denial_risk_pct=None, professional_denial_rates=[("prof-x", "Dr. X", 0.25, 8)])
    assert generate_insights(current, _EMPTY_PERIOD) == []


# --- Comparativo entre clínicas (build_network_comparativo_insight) ---


def test_network_comparativo_flagged_when_gap_above_threshold():
    insight = build_network_comparativo_insight(
        metric_label="Taxa de glosa", category="faturamento", your_rate=0.092, network_median=0.051, total_billed=70_000.0
    )
    assert insight is not None
    assert insight.severity == "comparativo"
    assert insight.category == "faturamento"
    assert insight.is_new is True
    assert "está acima da rede" in insight.title
    # impacto projetado = (0.092 - 0.051) * 70000 = 2870.0
    assert insight.financial_impact == pytest.approx(2870.0)


def test_network_comparativo_mentions_top_reason_when_provided():
    insight = build_network_comparativo_insight(
        metric_label="Taxa de glosa",
        category="faturamento",
        your_rate=0.092,
        network_median=0.051,
        total_billed=70_000.0,
        top_reason_label="faltou o código da doença (CID) no atendimento",
    )
    assert insight is not None
    assert "faltou o código da doença" in insight.message
    assert insight.action_label is not None
    assert insight.action_href == "#tab:comparativo"


def test_network_comparativo_omits_reason_note_when_not_provided():
    insight = build_network_comparativo_insight(
        metric_label="Taxa de falta", category="agenda", your_rate=0.092, network_median=0.051, total_billed=70_000.0
    )
    assert insight is not None
    assert "bom lugar pra" not in insight.message


def test_network_comparativo_mentions_worst_weekday_for_no_show_when_provided():
    """Mesmo 'por onde começar' que a variante de glosa já tinha (via
    top_reason_label), agora também pra taxa de falta — antes esta
    variante só mostrava o gap em R$, sem nenhuma pista de causa."""
    insight = build_network_comparativo_insight(
        metric_label="Taxa de falta",
        category="agenda",
        your_rate=0.092,
        network_median=0.051,
        total_billed=70_000.0,
        top_weekday_label="terça-feira",
    )
    assert insight is not None
    assert insight.category == "agenda"
    assert "terça-feira" in insight.message
    assert "bom lugar pra começar a agir" in insight.message


def test_describe_worst_no_show_weekday_picks_the_biggest_gap_above_average():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, weekday_no_show_counts={1: (6, 10), 3: (4, 9), 5: (1, 20)},
    )
    assert describe_worst_no_show_weekday(current) == "segunda-feira"


def test_describe_worst_no_show_weekday_is_none_without_enough_sample():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, weekday_no_show_counts={},
    )
    assert describe_worst_no_show_weekday(current) is None


def test_network_comparativo_absent_when_gap_is_trivial():
    # 5.1% vs 5.0% -> 0.1pp, bem abaixo do piso de 3pp — variação normal
    # entre clínicas parecidas, não é "notícia".
    insight = build_network_comparativo_insight(
        metric_label="Taxa de glosa", category="faturamento", your_rate=0.051, network_median=0.050, total_billed=70_000.0
    )
    assert insight is None


def test_network_comparativo_absent_without_billing_in_period():
    # Sem faturamento no período, a projeção em R$ não tem base — None,
    # nunca um card com impacto R$ 0,00 inventado.
    insight = build_network_comparativo_insight(
        metric_label="Taxa de glosa", category="faturamento", your_rate=0.092, network_median=0.051, total_billed=0.0
    )
    assert insight is None


def test_network_comparativo_enters_generate_insights_via_extra_insights():
    comparativo = build_network_comparativo_insight(
        metric_label="Taxa de glosa", category="faturamento", your_rate=0.30, network_median=0.05, total_billed=100_000.0
    )
    assert comparativo is not None
    insights = generate_insights(_EMPTY_PERIOD, _EMPTY_PERIOD, extra_insights=[comparativo])
    assert len(insights) == 1
    assert insights[0].severity == "comparativo"
