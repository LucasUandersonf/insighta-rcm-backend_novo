"""
tests/test_smart_insights_engine.py

Mesmo princípio de test_denial_risk_engine.py: o motor é puro (sem
banco), então testamos passando dataclasses já montados na mão, em
milissegundos, sem subir Postgres.
"""
import pytest

from app.services.smart_insights_engine import (
    _DENIAL_RISK_PCT_CRITICAL,
    _DENIAL_RISK_PCT_WARNING,
    DenialReasonCount,
    InsightsPeriodInput,
    build_network_comparativo_insight,
    describe_worst_no_show_weekday,
    generate_insights,
    is_true_denial_risk_reason,
    resolve_denial_risk_thresholds,
    suggest_denial_risk_thresholds,
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
    assert insights[0].action_href == "/painel?insurance_plan_id=plan-unimed-123"


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


def test_booking_channel_no_show_rate_above_average_is_flagged():
    """WhatsApp: 6 de 20 (30%) faltaram; telefone: 1 de 20 (5%) — média
    geral = 7/40 = 17.5%. WhatsApp fica 12.5pp acima (warning; crítico
    seria >=20pp)."""
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, booking_channel_no_show_counts={"whatsapp": (6, 20), "telefone": (1, 20)},
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    channel_titles = [i for i in insights if "falta mais" in i.title]
    assert len(channel_titles) == 1
    assert "whatsapp" in channel_titles[0].title.lower()
    assert channel_titles[0].severity == "warning"
    assert channel_titles[0].category == "agenda"


def test_booking_channel_no_show_rate_far_above_average_is_critical():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, booking_channel_no_show_counts={"site": (9, 20), "telefone": (1, 20)},  # site 45% vs média 25% -> +20pp
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    channel_titles = [i for i in insights if "falta mais" in i.title]
    assert len(channel_titles) == 1
    assert channel_titles[0].severity == "critical"


def test_booking_channel_no_show_rate_close_to_average_is_not_flagged():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, booking_channel_no_show_counts={"whatsapp": (3, 20), "telefone": (2, 20)},  # 15% vs 10%, média 12.5%
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_booking_channel_no_show_rate_small_sample_is_ignored_as_noise():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, booking_channel_no_show_counts={"whatsapp": (2, 2), "telefone": (1, 20)},
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_booking_channel_no_show_rate_only_the_worst_channel_becomes_a_card():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0,
        # whatsapp 60% (12/20), site 45% (9/20), telefone 5% (1/20) —
        # média geral = 22/60 = 36.7%. Só o pior (whatsapp) vira card.
        booking_channel_no_show_counts={"whatsapp": (12, 20), "site": (9, 20), "telefone": (1, 20)},
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    channel_titles = [i for i in insights if "falta mais" in i.title]
    assert len(channel_titles) == 1
    assert "whatsapp" in channel_titles[0].title.lower()


def test_cancellation_reason_concentration_is_flagged():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, total_cancelled_count=10,
        cancellation_reason_counts={"Paciente remarcou": 5, "Sala indisponível": 2},  # 50% -> warning
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    reason_titles = [i for i in insights if "mesmo motivo" in i.title]
    assert len(reason_titles) == 1
    assert "Paciente remarcou" in reason_titles[0].title
    assert "50%" in reason_titles[0].message
    assert reason_titles[0].severity == "warning"
    assert reason_titles[0].category == "agenda"


def test_cancellation_reason_high_concentration_is_critical():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, total_cancelled_count=10,
        cancellation_reason_counts={"Sala em manutenção": 7},  # 70% -> critical
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    reason_titles = [i for i in insights if "mesmo motivo" in i.title]
    assert len(reason_titles) == 1
    assert reason_titles[0].severity == "critical"


def test_cancellation_reason_uses_total_cancelled_as_denominator_not_sum_of_reasons():
    """Metade dos cancelamentos não tem motivo preenchido — o
    denominador é o TOTAL cancelado (20), não a soma dos motivos (10),
    senão o percentual seria inflado artificialmente (ver DECISÃO em
    AnalyticsRepository.cancellation_reason_breakdown)."""
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, total_cancelled_count=20,
        cancellation_reason_counts={"Paciente remarcou": 8},  # 8/20 = 40%, não 8/8 = 100%
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    reason_titles = [i for i in insights if "mesmo motivo" in i.title]
    assert len(reason_titles) == 1
    assert "40%" in reason_titles[0].message
    assert "100%" not in reason_titles[0].message


def test_cancellation_reason_scattered_reasons_is_not_flagged():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, total_cancelled_count=10,
        cancellation_reason_counts={"A": 3, "B": 3, "C": 2},  # nenhum motivo domina (máximo 30%)
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_cancellation_reason_small_sample_is_ignored_as_noise():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, total_cancelled_count=3,
        cancellation_reason_counts={"Paciente remarcou": 3},  # 100%, mas amostra pequena demais
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_opme_concentration_above_threshold_is_flagged():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, total_billed=10_000.0,
        item_type_charged_value={"material_opme": 2_500.0, "procedimento": 7_500.0},  # 25% -> acima do gatilho (20%)
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    opme_titles = [i for i in insights if "OPME" in i.title]
    assert len(opme_titles) == 1
    assert opme_titles[0].severity == "warning"
    assert opme_titles[0].category == "faturamento"
    assert opme_titles[0].financial_impact == 2_500.0
    assert "25%" in opme_titles[0].message


def test_opme_concentration_below_threshold_is_not_flagged():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, total_billed=10_000.0,
        item_type_charged_value={"material_opme": 500.0, "procedimento": 9_500.0},  # 5%
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_opme_concentration_absent_without_opme_billing():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, total_billed=10_000.0, item_type_charged_value={"procedimento": 10_000.0},
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_coparticipation_visibility_insight_with_enough_sample():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, coparticipation_total=850.0, coparticipation_billing_count=10,
        total_billing_count=20,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    copart_titles = [i for i in insights if "coparticipação" in i.title.lower()]
    assert len(copart_titles) == 1
    assert copart_titles[0].severity == "positive"
    assert copart_titles[0].category == "faturamento"
    assert "850" in copart_titles[0].message
    assert "50%" in copart_titles[0].message


def test_coparticipation_visibility_absent_with_small_sample():
    """Amostra pequena demais (menos de _MIN_COPARTICIPATION_SAMPLE
    faturamentos com coparticipação preenchida) — não dá pra confiar que
    o cliente já preenche essa coluna de forma consistente."""
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, coparticipation_total=50.0, coparticipation_billing_count=2,
        total_billing_count=20,
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_coparticipation_visibility_absent_without_any_data():
    assert generate_insights(_EMPTY_PERIOD, _EMPTY_PERIOD) == []


def test_opme_concentration_stable_across_periods_is_not_flagged():
    """Achado 4 da Auditoria (médio): uma clínica de perfil ortopédico
    tem concentração de OPME estruturalmente alta TODO período — sem
    comparar contra o período anterior, isso alertaria pra sempre. Aqui
    o período anterior já tinha a MESMA concentração (25%): não é uma
    mudança recente, é o perfil normal desta clínica."""
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, total_billed=8_000.0,
        item_type_charged_value={"material_opme": 2_000.0, "procedimento": 6_000.0},  # 25%
    )
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, total_billed=10_000.0,
        item_type_charged_value={"material_opme": 2_500.0, "procedimento": 7_500.0},  # também 25%
    )
    assert generate_insights(current, previous) == []


def test_opme_concentration_flags_only_when_it_increases_from_previous_period():
    """Mesma clínica ortopédica do teste acima, mas desta vez a
    concentração de fato SUBIU (25% -> 40%) — isso é uma mudança real,
    não o perfil estático da clínica, e deve alertar."""
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, total_billed=8_000.0,
        item_type_charged_value={"material_opme": 2_000.0, "procedimento": 6_000.0},  # 25%
    )
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, total_billed=10_000.0,
        item_type_charged_value={"material_opme": 4_000.0, "procedimento": 6_000.0},  # 40%
    )
    insights = generate_insights(current, previous)
    opme_titles = [i for i in insights if "OPME" in i.title]
    assert len(opme_titles) == 1
    assert "15" in opme_titles[0].message  # +15pp (40% - 25%)


def test_coparticipation_visibility_does_not_repeat_once_previous_period_also_has_sample():
    """Achado 4 da Auditoria (médio): segunda vez consecutiva que a
    amostra mínima é cruzada — o período ANTERIOR já tinha 8
    faturamentos com coparticipação (>= amostra mínima 5), então isso já
    não é 'a primeira vez que o dado passou a existir' — o card não deve
    reaparecer."""
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, coparticipation_total=700.0, coparticipation_billing_count=8,
        total_billing_count=18,
    )
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, coparticipation_total=850.0, coparticipation_billing_count=10,
        total_billing_count=20,
    )
    assert generate_insights(current, previous) == []


# ---------------------------------------------------------------------
# Raio-X da Receita — coparticipação como sinal CONTÍNUO (achado do
# Parecer Técnico "Boletim Insighta", revisão 2)
# ---------------------------------------------------------------------

def test_coparticipation_growth_insight_fires_when_share_increases_with_reliable_sample_both_periods():
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, total_billed=10_000.0, coparticipation_total=1_000.0,
        coparticipation_billing_count=8, total_billing_count=18,
    )
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, total_billed=10_000.0, coparticipation_total=2_000.0,
        coparticipation_billing_count=10, total_billing_count=20,
    )
    insights = generate_insights(current, previous)
    titles = [i for i in insights if "fatia de coparticipação" in i.title.lower()]
    assert len(titles) == 1
    assert titles[0].severity == "warning"
    assert titles[0].category == "faturamento"
    assert "20%" in titles[0].message
    assert "10" in titles[0].message  # +10pp (20% - 10%)


def test_coparticipation_growth_insight_absent_when_share_is_stable():
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, total_billed=8_000.0, coparticipation_total=800.0,
        coparticipation_billing_count=8, total_billing_count=18,
    )
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, total_billed=10_000.0, coparticipation_total=1_000.0,
        coparticipation_billing_count=10, total_billing_count=20,
    )
    assert generate_insights(current, previous) == []


def test_coparticipation_growth_insight_absent_when_previous_sample_is_too_small():
    """Mesmo raciocínio inverso do insight de 'estreia': sem amostra
    confiável no período anterior, não dá pra afirmar que a fatia
    'cresceu' — pode só ser o dado passando a existir agora, não uma
    tendência real.

    Amostra pequena no período anterior é EXATAMENTE o gatilho do
    insight de "estreia" (_coparticipation_visibility_insight), então
    ele dispara aqui — o que este teste verifica é que o de
    "tendência" (_coparticipation_growth_insight) não dispara junto."""
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, total_billed=10_000.0, coparticipation_total=100.0,
        coparticipation_billing_count=1, total_billing_count=18,
    )
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, total_billed=10_000.0, coparticipation_total=2_000.0,
        coparticipation_billing_count=10, total_billing_count=20,
    )
    titles = [insight.title for insight in generate_insights(current, previous)]
    assert "A fatia de coparticipação no seu faturamento está subindo" not in titles


def test_coparticipation_growth_insight_absent_without_any_data():
    assert generate_insights(_EMPTY_PERIOD, _EMPTY_PERIOD) == []


# ---------------------------------------------------------------------
# Épico F4.2 do Plano Diretor ("Fechar lacunas operacionais") — quanto
# do que foi COBRADO de coparticipação ainda não foi confirmado como
# recebido do paciente.
# ---------------------------------------------------------------------


def test_coparticipation_unconfirmed_insight_fires_with_enough_sample():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0,
        coparticipation_unconfirmed_value=250.0, coparticipation_unconfirmed_count=5,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    assert len(insights) == 1
    assert insights[0].severity == "warning"
    assert insights[0].financial_impact == 250.0
    assert "ainda não foi confirmada" in insights[0].title.lower()


def test_coparticipation_unconfirmed_insight_absent_below_min_sample():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0,
        coparticipation_unconfirmed_value=50.0, coparticipation_unconfirmed_count=2,
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_coparticipation_unconfirmed_insight_absent_without_any_unconfirmed_value():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0,
        coparticipation_unconfirmed_value=0.0, coparticipation_unconfirmed_count=0,
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


# ---------------------------------------------------------------------
# "Equilíbrio Insighta" (Balanced Scorecard, perna Cliente, mecanismo 5)
# — quanto da coparticipação foi cobrado por uma forma de pagamento que
# não garante recebimento (boleto/cartão de crédito parcelado), diferente
# do insight de "ninguém confirmou ainda" acima.
# ---------------------------------------------------------------------


def test_coparticipation_delayed_payment_insight_fires_with_enough_sample():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0,
        coparticipation_delayed_payment_value=300.0, coparticipation_delayed_payment_count=5,
        coparticipation_known_payment_method_value=500.0,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    assert len(insights) == 1
    assert insights[0].severity == "warning"
    assert insights[0].financial_impact == 300.0
    assert "60%" in insights[0].message
    assert "boleto" in insights[0].message.lower()


def test_coparticipation_delayed_payment_insight_absent_below_min_sample():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0,
        coparticipation_delayed_payment_value=60.0, coparticipation_delayed_payment_count=2,
        coparticipation_known_payment_method_value=100.0,
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_coparticipation_delayed_payment_insight_absent_without_any_known_payment_method():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0,
        coparticipation_delayed_payment_value=0.0, coparticipation_delayed_payment_count=0,
        coparticipation_known_payment_method_value=0.0,
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_coparticipation_delayed_payment_insight_is_independent_from_unconfirmed_insight():
    """Os dois cobrem lacunas DIFERENTES — nada impede os dois
    dispararem juntos no mesmo período (confirmação manual e forma de
    pagamento são sinais independentes)."""
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0,
        coparticipation_unconfirmed_value=250.0, coparticipation_unconfirmed_count=5,
        coparticipation_delayed_payment_value=300.0, coparticipation_delayed_payment_count=5,
        coparticipation_known_payment_method_value=500.0,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    titles = {i.title.lower() for i in insights}
    assert len(insights) == 2
    assert any("ainda não foi confirmada" in t for t in titles)
    assert any("ainda pode não fechar" in t for t in titles)


# ---------------------------------------------------------------------
# Épico F2.3 do Plano Diretor ("Auditoria documental leve — prontuário ×
# conta") — quanto do que foi cobrado como OPME ainda não foi conferido
# quanto à presença de prescrição/evolução no prontuário.
# ---------------------------------------------------------------------


def test_opme_documentation_unconfirmed_insight_fires_with_enough_sample():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0,
        opme_documentation_unconfirmed_value=1600.0, opme_documentation_unconfirmed_count=2,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    assert len(insights) == 1
    assert insights[0].severity == "warning"
    assert insights[0].financial_impact == 1600.0
    assert "sem conferência documental" in insights[0].title.lower()


def test_opme_documentation_unconfirmed_insight_absent_below_min_sample():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0,
        opme_documentation_unconfirmed_value=800.0, opme_documentation_unconfirmed_count=1,
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_opme_documentation_unconfirmed_insight_absent_without_any_unconfirmed_value():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0,
        opme_documentation_unconfirmed_value=0.0, opme_documentation_unconfirmed_count=0,
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


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


# ---------------------------------------------------------------------
# Épico F2.1 do Plano Diretor ("Calibração por especialidade/porte") —
# limiares de risco de glosa configuráveis por tenant.
# ---------------------------------------------------------------------


def test_custom_denial_risk_thresholds_change_what_gets_flagged():
    """Com limiares customizados mais folgados, um risco que dispararia
    'warning' nos defaults deixa de aparecer — prova que generate_insights
    de fato usa o valor passado, não a constante do módulo."""
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, denial_risk_pct=20.0, denial_at_risk_value=1000.0,
    )
    # Default (_DENIAL_RISK_PCT_WARNING=15.0): 20% dispara warning.
    assert len(generate_insights(current, _EMPTY_PERIOD)) == 1
    # Limiar customizado mais alto: 20% fica abaixo do novo "aviso".
    insights = generate_insights(
        current, _EMPTY_PERIOD, denial_risk_warning_threshold=25.0, denial_risk_critical_threshold=50.0
    )
    assert insights == []


def test_custom_denial_risk_thresholds_can_reclassify_severity():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, denial_risk_pct=20.0, denial_at_risk_value=1000.0,
    )
    # Default: 20% < _DENIAL_RISK_PCT_CRITICAL (40.0) -> warning.
    default_insights = generate_insights(current, _EMPTY_PERIOD)
    assert default_insights[0].severity == "warning"
    # Limiar crítico customizado mais baixo que 20% -> vira critical.
    insights = generate_insights(
        current, _EMPTY_PERIOD, denial_risk_warning_threshold=5.0, denial_risk_critical_threshold=15.0
    )
    assert insights[0].severity == "critical"


def test_resolve_denial_risk_thresholds_none_tenant_uses_defaults():
    assert resolve_denial_risk_thresholds(None) == (_DENIAL_RISK_PCT_WARNING, _DENIAL_RISK_PCT_CRITICAL)


class _FakeTenant:
    def __init__(self, warning=None, critical=None):
        self.denial_risk_warning_threshold = warning
        self.denial_risk_critical_threshold = critical


def test_resolve_denial_risk_thresholds_tenant_without_config_uses_defaults():
    assert resolve_denial_risk_thresholds(_FakeTenant()) == (_DENIAL_RISK_PCT_WARNING, _DENIAL_RISK_PCT_CRITICAL)


def test_resolve_denial_risk_thresholds_tenant_with_config():
    assert resolve_denial_risk_thresholds(_FakeTenant(warning=10.0, critical=30.0)) == (10.0, 30.0)


def test_suggest_denial_risk_thresholds_below_min_sample_returns_none():
    assert suggest_denial_risk_thresholds([10.0, 20.0, 30.0]) is None


def test_suggest_denial_risk_thresholds_with_enough_months():
    # 6 meses (MIN_MONTHS_FOR_DENIAL_RISK_SUGGESTION), distribuição
    # crescente simples.
    monthly_pcts = [5.0, 10.0, 15.0, 20.0, 25.0, 30.0]
    suggestion = suggest_denial_risk_thresholds(monthly_pcts)
    assert suggestion is not None
    assert suggestion.sample_size == 6
    assert suggestion.warning_threshold < suggestion.critical_threshold
    # Mediana de [5,10,15,20,25,30] é 17.5.
    assert suggestion.warning_threshold == 17.5


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


# "O que resta em aberto" da Auditoria de Templates e Insights: peça
# natural do mesmo padrão que Guia/coparticipação já fecharam —
# core.lotes.status/closed_at (Fase 2) já modelados, sem nenhum insight
# consumindo até esta rodada.


def test_stale_open_lotes_insight_fires_with_count_and_oldest_age():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, stale_open_lotes_count=3, oldest_open_lote_age_days=45,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    lote_titles = [i for i in insights if "lote" in i.title.lower()]
    assert len(lote_titles) == 1
    assert lote_titles[0].severity == "warning"
    assert lote_titles[0].category == "faturamento"
    assert "3 lotes" in lote_titles[0].message
    assert "45 dias" in lote_titles[0].message
    # LotesPage.tsx agora existe no frontend — o botão aponta pra ela.
    assert lote_titles[0].action_href == "/lotes"


def test_stale_open_lotes_insight_singular_wording_and_no_age_note():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, stale_open_lotes_count=1, oldest_open_lote_age_days=None,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    lote_titles = [i for i in insights if "lote" in i.title.lower()]
    assert len(lote_titles) == 1
    assert "1 lote " in lote_titles[0].message  # singular, sem "s"
    assert "mais antigo" not in lote_titles[0].message  # sem idade, sem a frase extra


def test_stale_open_lotes_insight_absent_without_any_stale_lote():
    assert generate_insights(_EMPTY_PERIOD, _EMPTY_PERIOD) == []


def test_stale_open_lotes_insight_is_current_period_state_never_from_previous():
    """Mesmo raciocínio de appeals_due_soon_count: estado 'AGORA', nunca
    lido do período anterior — mesmo que `previous` também tivesse lotes
    parados, isso nunca deveria gerar um segundo card nem influenciar o
    card do período atual."""
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, stale_open_lotes_count=2, oldest_open_lote_age_days=10,
    )
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, stale_open_lotes_count=99, oldest_open_lote_age_days=900,
    )
    insights = generate_insights(current, previous)
    lote_titles = [i for i in insights if "lote" in i.title.lower()]
    assert len(lote_titles) == 1
    assert "2 lotes" in lote_titles[0].message
    assert "10 dias" in lote_titles[0].message


# PMR (Prazo Médio de Recebimento) — achado da auditoria "Veredito do
# Gestor Clínico" (Seção 4, Achado 2): billing.created_at/settled_at
# sempre existiram no banco, mas nenhum indicador calculava essa
# diferença até esta rodada.


def test_payment_lag_insight_fires_above_warning_threshold():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, avg_days_to_receive=65.0, payment_lag_settled_count=10,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    lag_titles = [i for i in insights if "demorando" in i.title.lower()]
    assert len(lag_titles) == 1
    assert lag_titles[0].severity == "warning"
    assert lag_titles[0].category == "faturamento"
    assert "65 dias" in lag_titles[0].message
    assert lag_titles[0].action_href == "/"


def test_payment_lag_insight_critical_above_90_days():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, avg_days_to_receive=95.0, payment_lag_settled_count=10,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    lag_titles = [i for i in insights if "demorando" in i.title.lower()]
    assert len(lag_titles) == 1
    assert lag_titles[0].severity == "critical"
    # Acima do benchmark de mercado (~69 dias, ANAHP 2024) — a mensagem cita isso.
    assert "média do setor" in lag_titles[0].message


def test_payment_lag_insight_absent_below_warning_threshold():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, avg_days_to_receive=45.0, payment_lag_settled_count=10,
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_payment_lag_insight_absent_with_small_sample():
    """Amostra pequena demais (menos de _MIN_PAYMENT_LAG_SAMPLE billings
    conciliados) — mesmo raciocínio de amostra mínima do resto do
    arquivo: 1-2 casos isolados não provam nada sobre o prazo médio real
    da clínica."""
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, avg_days_to_receive=90.0, payment_lag_settled_count=2,
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_payment_lag_insight_absent_without_any_settled_billing():
    assert generate_insights(_EMPTY_PERIOD, _EMPTY_PERIOD) == []


def test_payment_lag_insight_notes_when_trend_is_worsening():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, avg_days_to_receive=70.0, payment_lag_settled_count=10,
    )
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, avg_days_to_receive=55.0, payment_lag_settled_count=10,
    )
    insights = generate_insights(current, previous)
    lag_titles = [i for i in insights if "demorando" in i.title.lower()]
    assert len(lag_titles) == 1
    assert "piorando" in lag_titles[0].message
    assert "15 dias" in lag_titles[0].message


def test_payment_lag_insight_no_trend_note_when_previous_sample_is_too_small():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, avg_days_to_receive=70.0, payment_lag_settled_count=10,
    )
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, avg_days_to_receive=20.0, payment_lag_settled_count=1,
    )
    insights = generate_insights(current, previous)
    lag_titles = [i for i in insights if "demorando" in i.title.lower()]
    assert len(lag_titles) == 1
    assert "piorando" not in lag_titles[0].message


# ---------------------------------------------------------------------
# Raio-X da Receita — contrato de convênio vencendo sem renovação
# ---------------------------------------------------------------------

def test_contract_expiring_insight_fires_with_count_and_soonest_plan():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, expiring_contracts_count=2,
        soonest_expiring_contract_plan_name="Bradesco Saúde", soonest_expiring_contract_days=20,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    titles = [i for i in insights if "vencendo" in i.title.lower()]
    assert len(titles) == 1
    assert titles[0].severity == "warning"
    assert titles[0].category == "faturamento"
    assert "2 contratos" in titles[0].message
    assert "Bradesco Saúde" in titles[0].message
    assert "em 20 dias" in titles[0].message
    assert titles[0].action_href == "/contracts"


def test_contract_expiring_insight_singular_wording():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, expiring_contracts_count=1,
        soonest_expiring_contract_plan_name="Amil", soonest_expiring_contract_days=0,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    titles = [i for i in insights if "vencendo" in i.title.lower()]
    assert len(titles) == 1
    assert "1 contrato de repasse vencendo" in titles[0].message
    assert "vence hoje" in titles[0].message


def test_contract_expiring_insight_is_critical_within_the_critical_window():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, expiring_contracts_count=1,
        soonest_expiring_contract_plan_name="Amil", soonest_expiring_contract_days=5,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    titles = [i for i in insights if "vencendo" in i.title.lower()]
    assert titles[0].severity == "critical"


def test_contract_expiring_insight_absent_without_any_expiring_contract():
    assert generate_insights(_EMPTY_PERIOD, _EMPTY_PERIOD) == []


def test_contract_expiring_insight_is_current_period_state_never_from_previous():
    """Estado 'AGORA' — o campo só existe em `current`; um valor
    diferente em `previous` nunca é lido (mesmo raciocínio de
    test_stale_open_lotes_insight_is_current_period_state_never_from_previous)."""
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, expiring_contracts_count=9,
        soonest_expiring_contract_plan_name="Outro Convênio", soonest_expiring_contract_days=1,
    )
    insights = generate_insights(_EMPTY_PERIOD, previous)
    assert [i for i in insights if "vencendo" in i.title.lower()] == []


# ---------------------------------------------------------------------
# Raio-X da Receita — concentração de receita em poucos convênios
# ---------------------------------------------------------------------

def test_revenue_concentration_insight_fires_above_warning_threshold():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, total_billed=10_000.0,
        revenue_by_plan={"Unimed": 6_500.0, "Bradesco Saúde": 3_500.0},
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    titles = [i for i in insights if "depende de um único convênio" in i.title.lower()]
    assert len(titles) == 1
    assert titles[0].severity == "warning"
    assert titles[0].category == "faturamento"
    assert "Unimed" in titles[0].title
    assert "65%" in titles[0].message
    assert titles[0].financial_impact == 6_500.0


def test_revenue_concentration_insight_is_critical_above_critical_threshold():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, total_billed=10_000.0,
        revenue_by_plan={"Unimed": 8_500.0, "Bradesco Saúde": 1_500.0},
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    titles = [i for i in insights if "depende de um único convênio" in i.title.lower()]
    assert titles[0].severity == "critical"


def test_revenue_concentration_insight_absent_below_threshold():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, total_billed=10_000.0,
        revenue_by_plan={"Unimed": 5_000.0, "Bradesco Saúde": 5_000.0},
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_revenue_concentration_insight_absent_with_a_single_contracted_plan():
    """Com 1 só convênio faturado no período, 100% de concentração é a
    estrutura do negócio (clínica fechada com uma única operadora), não
    uma anomalia — mesmo raciocínio do piso `_MIN_PLANS_FOR_CONCENTRATION`."""
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, total_billed=10_000.0,
        revenue_by_plan={"Unimed": 10_000.0},
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_revenue_concentration_insight_absent_without_any_billing():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, total_billed=0.0, revenue_by_plan={},
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


# ---------------------------------------------------------------------
# Raio-X da Receita — ROI de marketing no feed de insights
# ---------------------------------------------------------------------

def test_marketing_roi_insight_fires_when_spend_outpaces_attributed_revenue():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, marketing_spend_total=1_000.0, marketing_revenue_attributed=600.0,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    titles = [i for i in insights if "marketing" in i.title.lower()]
    assert len(titles) == 1
    assert titles[0].severity == "warning"
    assert titles[0].category == "faturamento"
    assert "-40%" in titles[0].message
    assert titles[0].financial_impact == 400.0


def test_marketing_roi_insight_is_critical_when_revenue_is_below_half_of_spend():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, marketing_spend_total=1_000.0, marketing_revenue_attributed=300.0,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    titles = [i for i in insights if "marketing" in i.title.lower()]
    assert titles[0].severity == "critical"


def test_marketing_roi_insight_absent_when_roi_is_positive():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, marketing_spend_total=1_000.0, marketing_revenue_attributed=1_500.0,
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_marketing_roi_insight_absent_below_minimum_spend_floor():
    """Gasto pequeno demais (< _MIN_MARKETING_SPEND_FOR_INSIGHT) com ROI
    negativo é ruído de teste de campanha, não um padrão — mesmo
    raciocínio de amostra mínima do resto do arquivo."""
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, marketing_spend_total=50.0, marketing_revenue_attributed=0.0,
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_marketing_roi_insight_absent_without_any_spend():
    assert generate_insights(_EMPTY_PERIOD, _EMPTY_PERIOD) == []


def test_marketing_roi_insight_notes_when_trend_is_worsening():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, marketing_spend_total=1_000.0, marketing_revenue_attributed=500.0,
    )
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, marketing_spend_total=1_000.0, marketing_revenue_attributed=950.0,
    )
    insights = generate_insights(current, previous)
    titles = [i for i in insights if "marketing" in i.title.lower()]
    assert "piorando" in titles[0].message


def test_marketing_roi_insight_no_trend_note_when_previous_spend_is_below_floor():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, marketing_spend_total=1_000.0, marketing_revenue_attributed=500.0,
    )
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, marketing_spend_total=10.0, marketing_revenue_attributed=100.0,
    )
    insights = generate_insights(current, previous)
    titles = [i for i in insights if "marketing" in i.title.lower()]
    assert "piorando" not in titles[0].message


# ---------------------------------------------------------------------
# Raio-X da Receita — glosa paga a menor sem recurso aberto
# ---------------------------------------------------------------------

def test_payment_gap_without_appeal_insight_fires_with_count_and_value():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, payment_gap_without_appeal_count=3, payment_gap_without_appeal_value=450.0,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    titles = [i for i in insights if "ninguém contestou" in i.title.lower()]
    assert len(titles) == 1
    assert titles[0].severity == "critical"
    assert titles[0].category == "faturamento"
    assert "3 contas" in titles[0].message
    assert "450,00" in titles[0].message or "450.00" in titles[0].message
    assert titles[0].financial_impact == 450.0
    assert titles[0].action_href == "/denial-appeals"


def test_payment_gap_without_appeal_insight_singular_wording():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, payment_gap_without_appeal_count=1, payment_gap_without_appeal_value=150.0,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    titles = [i for i in insights if "ninguém contestou" in i.title.lower()]
    assert "1 conta onde" in titles[0].message


def test_payment_gap_without_appeal_insight_absent_without_any_backlog():
    assert generate_insights(_EMPTY_PERIOD, _EMPTY_PERIOD) == []


def test_payment_gap_without_appeal_insight_is_current_period_state_never_from_previous():
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, payment_gap_without_appeal_count=9, payment_gap_without_appeal_value=9_000.0,
    )
    insights = generate_insights(_EMPTY_PERIOD, previous)
    assert [i for i in insights if "ninguém contestou" in i.title.lower()] == []


# ---------------------------------------------------------------------
# Raio-X da Receita — sazonalidade de agenda (ano contra ano)
# ---------------------------------------------------------------------

def test_yoy_seasonality_insight_fires_above_warning_threshold():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, weekday_appointment_counts={0: 40}, yoy_last_year_appointment_count=60,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    titles = [i for i in insights if "mesmo período do ano passado" in i.title.lower()]
    assert len(titles) == 1
    assert titles[0].severity == "warning"
    assert titles[0].category == "agenda"
    assert "60 consulta" in titles[0].message
    assert "agora são 40" in titles[0].message
    assert "33%" in titles[0].message


def test_yoy_seasonality_insight_is_critical_above_critical_threshold():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, weekday_appointment_counts={0: 30}, yoy_last_year_appointment_count=100,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    titles = [i for i in insights if "mesmo período do ano passado" in i.title.lower()]
    assert titles[0].severity == "critical"


def test_yoy_seasonality_insight_absent_below_warning_threshold():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, weekday_appointment_counts={0: 90}, yoy_last_year_appointment_count=100,
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_yoy_seasonality_insight_absent_when_current_grew_vs_last_year():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, weekday_appointment_counts={0: 150}, yoy_last_year_appointment_count=100,
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_yoy_seasonality_insight_absent_with_small_last_year_sample():
    """Amostra pequena no ano passado (clínica nova, ou período de baixo
    volume histórico) — qualquer variação percentual seria ruído, mesmo
    raciocínio de amostra mínima do resto do arquivo."""
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, weekday_appointment_counts={0: 1}, yoy_last_year_appointment_count=5,
    )
    assert generate_insights(current, _EMPTY_PERIOD) == []


def test_yoy_seasonality_insight_absent_without_last_year_data():
    assert generate_insights(_EMPTY_PERIOD, _EMPTY_PERIOD) == []


# ---------------------------------------------------------------------
# Raio-X da Receita — churn antecipado de paciente
# ---------------------------------------------------------------------

def test_early_churn_insight_fires_with_count():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, early_churn_risk_count=4,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    titles = [i for i in insights if "sumindo do próprio padrão" in i.title.lower()]
    assert len(titles) == 1
    assert titles[0].severity == "warning"
    assert titles[0].category == "agenda"
    assert "4 pacientes" in titles[0].message
    assert titles[0].action_href == "#carteira-inativa"


def test_early_churn_insight_singular_wording():
    current = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, early_churn_risk_count=1,
    )
    insights = generate_insights(current, _EMPTY_PERIOD)
    titles = [i for i in insights if "sumindo do próprio padrão" in i.title.lower()]
    assert "1 paciente já" in titles[0].message


def test_early_churn_insight_absent_without_any_risk():
    assert generate_insights(_EMPTY_PERIOD, _EMPTY_PERIOD) == []


def test_early_churn_insight_is_current_period_state_never_from_previous():
    previous = InsightsPeriodInput(
        denial_reason_counts=[], financial_hole_total=0, total_value_saved=0, avg_capacity_utilization=None,
        high_risk_no_show_count=0, early_churn_risk_count=9,
    )
    insights = generate_insights(_EMPTY_PERIOD, previous)
    assert [i for i in insights if "sumindo do próprio padrão" in i.title.lower()] == []
