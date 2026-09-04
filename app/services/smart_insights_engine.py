"""
app/services/smart_insights_engine.py

Painel de Insights Descritivos e Acionáveis (Sprint de Dashboards de
Decisão): traduz números agregados em texto e recomendação, priorizado
por impacto financeiro. Mesmo princípio de denial_risk_engine.py — regras
determinísticas e explicáveis (não um modelo de ML de caixa-preta): a
diretoria precisa conseguir responder "por que esse alerta apareceu?"
com uma frase objetiva.

Todas as funções aqui são PURAS (recebem dataclasses já calculados,
nunca tocam banco) — testáveis isoladamente, sem Postgres, como
test_denial_risk_engine.py e test_no_show_risk_engine.py fazem para os
outros motores.
"""
from dataclasses import dataclass, field

# Rótulos legíveis para os reason_code do motor de glosa (denial_risk_engine.py)
_REASON_LABELS = {
    "missing_cid": "ausência de CID",
    "missing_procedure_code": "ausência de código de procedimento",
    "no_contract_reference": "falta de contrato de referência cadastrado",
    "value_above_contract": "cobrança acima do valor contratado",
    "value_below_contract_revenue_leak": "cobrança abaixo do valor contratado",
}

# Amostra mínima antes de declarar uma variação percentual "spike" —
# mesma lógica de MIN_SAMPLE_SIZE em no_show_risk_engine.py: 2 casos indo
# para 3 é "+50%" mas não é um padrão, é ruído estatístico.
_MIN_SAMPLE_FOR_TREND = 3
_SPIKE_THRESHOLD_PCT = 15.0
_HIGH_RISK_NO_SHOW_ALERT_THRESHOLD = 5
_UTILIZATION_DROP_ALERT_PP = 10.0  # pontos percentuais

# Auditoria Go-Live — redesenho "menos BI" do painel: dois novos insights
# textuais, mesmo padrão de threshold nomeado e amostra mínima dos demais.
_WEEKDAY_LABELS = ("domingo", "segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado")
_MIN_WEEKDAY_SAMPLE = 3  # mesmo raciocínio de _MIN_SAMPLE_FOR_TREND: amostra baixa vira ruído, não padrão
_WEEKDAY_DROP_CRITICAL_PCT = 30.0
_WEEKDAY_DROP_WARNING_PCT = 15.0
_DENIAL_RISK_PCT_CRITICAL = 40.0
_DENIAL_RISK_PCT_WARNING = 15.0

# Achado do usuário sobre lacunas do módulo de Agenda: volume por dia da
# semana (weekday_appointment_counts acima) não responde "quinta tem taxa
# de falta alta" — só "quinta tem menos gente marcado". Comparação é
# INTRA-período (o dia contra a MÉDIA do próprio período), não período
# a período como _weekday_drop_insights: um corte absoluto (ex: "acima de
# 30%") não se adapta ao perfil de cada clínica/especialidade, mas "este
# dia está X pontos acima da sua própria média" é sempre acionável.
_WEEKDAY_NO_SHOW_RATE_CRITICAL_PP = 20.0  # pontos percentuais acima da média do período
_WEEKDAY_NO_SHOW_RATE_WARNING_PP = 10.0

# Terceiro exemplo do briefing de redesenho: meta anual vs. ritmo real.
# "Atrás do ritmo" é medido contra o esperado NA DATA DE HOJE (meta *
# fração do ano decorrida), não contra a meta inteira — do contrário
# todo dia antes de 31/dez estaria "abaixo da meta" por definição.
_ANNUAL_GOAL_BEHIND_WARNING_PCT = 10.0
_ANNUAL_GOAL_BEHIND_CRITICAL_PCT = 25.0


@dataclass
class DenialReasonCount:
    plan_name: str
    reason_code: str
    count: int


@dataclass
class InsightsPeriodInput:
    """Tudo que o motor precisa para UM período (atual ou anterior) —
    monta a partir de AnalyticsRepository + ReportingRepository no
    AnalyticsService, sem o motor nunca ver a sessão de banco."""

    denial_reason_counts: list[DenialReasonCount]
    financial_hole_total: float  # Divergência de Cobrança (cobrado < contratado)
    total_value_saved: float
    avg_capacity_utilization: float | None  # 0.0 a 1.0
    high_risk_no_show_count: int
    # Divergência de Recebimento (pago pela operadora < contratado, só
    # billings já conciliados) — default 0.0 para não quebrar chamadas
    # antigas que ainda não passam esse dado (ver AnalyticsService).
    payment_gap_total: float = 0.0
    # Recursos de glosa (ver app/sql/008_denial_appeals.sql) com prazo
    # vencendo em breve — estado "AGORA", não do período (ver DECISÃO em
    # AnalyticsService._period_insights_input: só o período atual recebe
    # o valor real, o anterior fica 0 de propósito).
    appeals_due_soon_count: int = 0
    # Volume de agendamentos por dia da semana (0=domingo..6=sábado — ver
    # AnalyticsRepository.appointment_weekday_histogram) — alimenta
    # _weekday_drop_insights. Default {} para não quebrar chamadas/testes
    # existentes que ainda não passam esse dado.
    weekday_appointment_counts: dict[int, int] = field(default_factory=dict)
    # Taxa de falta por dia da semana — {weekday: (no_show_count, total_
    # resolvido)} — ver AnalyticsRepository.weekday_no_show_rate_breakdown
    # e _weekday_no_show_rate_insights. Só faz sentido em `current` (a
    # comparação é intra-período, contra a própria média do período, não
    # período a período), mas o default {} existe pelo mesmo motivo de
    # weekday_appointment_counts: não quebrar chamadas/testes existentes.
    weekday_no_show_counts: dict[int, tuple[int, int]] = field(default_factory=dict)
    # % do valor faturado no período com denial_risk_level medium/high
    # (ver AnalyticsRepository.denial_risk_value_breakdown), e o valor em
    # R$ correspondente — None quando não há faturamento no período (%
    # sobre base zero é indefinida, mesmo princípio de _delta_pct).
    denial_risk_pct: float | None = None
    denial_at_risk_value: float = 0.0
    # --- Meta de faturamento anual (terceiro exemplo do briefing) ---
    # Todos com default para não quebrar chamadas/testes existentes, e
    # todos só fazem sentido em `current` (não há "meta do período
    # anterior" — é um estado presente, mesmo raciocínio de
    # appeals_due_soon_count). None em qualquer um dos dois primeiros
    # campos = "não gerar o insight" (sem meta configurada, ou sem
    # contexto de data para calcular o ritmo esperado).
    annual_revenue_goal: float | None = None  # Tenant.annual_revenue_goal — NUNCA calculado, só o valor manual
    elapsed_year_fraction: float | None = None  # 0.0 a 1.0 — fração do ano calendário já decorrida (calculado pelo service, não pelo motor, para manter esta função pura/testável)
    ytd_billed_total: float = 0.0  # faturamento acumulado do ano até hoje
    inactive_patients_count: int = 0  # pacientes sem atendimento há mais de 1 ano — nutre a recomendação de CRM


@dataclass
class Insight:
    severity: str  # "critical" | "warning" | "positive"
    title: str
    message: str
    financial_impact: float | None = None  # em R$; usado só para ordenar por relevância


def _reason_label(code: str) -> str:
    return _REASON_LABELS.get(code, code)


def _index_reason_counts(counts: list[DenialReasonCount]) -> dict[tuple[str, str], int]:
    return {(c.plan_name, c.reason_code): c.count for c in counts}


def _denial_spike_insights(current: InsightsPeriodInput, previous: InsightsPeriodInput) -> list[Insight]:
    current_idx = _index_reason_counts(current.denial_reason_counts)
    previous_idx = _index_reason_counts(previous.denial_reason_counts)

    insights: list[Insight] = []
    for (plan_name, reason_code), current_count in current_idx.items():
        previous_count = previous_idx.get((plan_name, reason_code), 0)
        reason_label = _reason_label(reason_code)

        if previous_count == 0:
            # Padrão novo, sem histórico de comparação — só alerta se já
            # tem volume suficiente para não ser ruído (ver _MIN_SAMPLE_FOR_TREND).
            if current_count >= _MIN_SAMPLE_FOR_TREND:
                insights.append(
                    Insight(
                        severity="critical",
                        title=f"Novo padrão de glosa: {plan_name}",
                        message=(
                            f"A operadora {plan_name} não registrava glosas por {reason_label} no período anterior "
                            f"e agora soma {current_count} caso(s) nesta janela. Recomendamos revisar o lote antes "
                            "de faturar."
                        ),
                    )
                )
            continue

        if previous_count < _MIN_SAMPLE_FOR_TREND:
            continue  # amostra anterior baixa demais para "variação %" significar algo

        growth_pct = ((current_count - previous_count) / previous_count) * 100
        if growth_pct >= _SPIKE_THRESHOLD_PCT:
            insights.append(
                Insight(
                    severity="critical",
                    title=f"Salto de glosas: {plan_name}",
                    message=(
                        f"A operadora {plan_name} apresentou um salto de {growth_pct:.0f}% em glosas por "
                        f"{reason_label} nesta janela (de {previous_count} para {current_count} casos). "
                        "Recomendamos travar o faturamento deste lote até revisão."
                    ),
                )
            )
    return insights


def _financial_hole_insight(current: InsightsPeriodInput, previous: InsightsPeriodInput) -> Insight | None:
    if current.financial_hole_total <= 0:
        return None
    delta = current.financial_hole_total - previous.financial_hole_total
    trend = "em alta" if delta > 0 else "estável ou em queda"
    return Insight(
        severity="warning",
        title="Buraco financeiro identificado",
        message=(
            f"R$ {current.financial_hole_total:,.2f} cobrados abaixo do valor contratado nesta janela "
            f"({trend} em relação ao período anterior). Esse valor não é glosa — é receita que já deixou "
            "de entrar por cobrança abaixo da tabela."
        ),
        financial_impact=current.financial_hole_total,
    )


def _payment_gap_insight(current: InsightsPeriodInput, previous: InsightsPeriodInput) -> Insight | None:
    if current.payment_gap_total <= 0:
        return None
    delta = current.payment_gap_total - previous.payment_gap_total
    trend = "em alta" if delta > 0 else "estável ou em queda"
    return Insight(
        severity="critical",
        title="Divergência de recebimento com operadora",
        message=(
            f"R$ {current.payment_gap_total:,.2f} pagos pelas operadoras ABAIXO do valor contratado nesta "
            f"janela, em billings já conciliados ({trend} em relação ao período anterior). Diferente do "
            "buraco de cobrança, aqui a clínica cobrou certo e a operadora pagou a menos — vale contestação "
            "junto ao convênio."
        ),
        financial_impact=current.payment_gap_total,
    )


def _value_saved_insight(current: InsightsPeriodInput, previous: InsightsPeriodInput) -> Insight | None:
    if current.total_value_saved <= 0:
        return None
    delta = current.total_value_saved - previous.total_value_saved
    if delta <= 0:
        return None  # só celebra quando o número de fato melhorou
    return Insight(
        severity="positive",
        title="Eficiência do motor anti-glosa em alta",
        message=(
            f"R$ {current.total_value_saved:,.2f} protegidos por correções automáticas nesta janela — "
            f"R$ {delta:,.2f} a mais que no período anterior."
        ),
        financial_impact=current.total_value_saved,
    )


def _capacity_drop_insight(
    current: InsightsPeriodInput, previous: InsightsPeriodInput, estimated_idle_capacity_revenue_lost: float
) -> Insight | None:
    if current.avg_capacity_utilization is None or previous.avg_capacity_utilization is None:
        return None
    drop_pp = (previous.avg_capacity_utilization - current.avg_capacity_utilization) * 100
    if drop_pp >= _UTILIZATION_DROP_ALERT_PP:
        # Mesmo padrão do insight de no-show (_no_show_risk_insight): a
        # queda em pontos percentuais diz O QUE mudou, mas é o R$ que
        # decide a prioridade do alerta na lista (ver generate_insights,
        # ordenado por financial_impact) — ver DECISÃO em
        # capacity_service.estimate_idle_capacity_revenue_lost.
        impact_note = (
            f" — receita cessante estimada de R$ {estimated_idle_capacity_revenue_lost:,.2f} nesta janela"
            if estimated_idle_capacity_revenue_lost > 0
            else ""
        )
        return Insight(
            severity="warning",
            title="Ocupação de agenda em queda",
            message=(
                f"A taxa média de ocupação da agenda caiu {drop_pp:.0f} pontos percentuais em relação ao "
                f"período anterior{impact_note}. Vale checar ociosidade por profissional no painel de "
                "Agenda & Capacidade."
            ),
            financial_impact=estimated_idle_capacity_revenue_lost or None,
        )
    return None


def _no_show_risk_insight(current: InsightsPeriodInput, estimated_revenue_at_risk: float) -> Insight | None:
    if current.high_risk_no_show_count < _HIGH_RISK_NO_SHOW_ALERT_THRESHOLD:
        return None
    return Insight(
        severity="warning",
        title="Volume alto de agendamentos com risco de falta",
        message=(
            f"{current.high_risk_no_show_count} agendamento(s) com risco ALTO de no-show nesta janela — "
            f"receita cessante estimada de R$ {estimated_revenue_at_risk:,.2f} se as faltas se confirmarem."
        ),
        financial_impact=estimated_revenue_at_risk,
    )


def _weekday_drop_insights(current: InsightsPeriodInput, previous: InsightsPeriodInput) -> list[Insight]:
    """
    Traduz a comparação de agenda por dia da semana em texto acionável —
    exatamente o exemplo do briefing de redesenho: em vez de um número
    frio de ocupação média, aponta QUAL dia caiu e por QUANTO, para o
    gestor saber onde agir sem precisar cruzar números sozinho.

    Só compara dias que têm amostra mínima no período ANTERIOR
    (_MIN_WEEKDAY_SAMPLE) — sem isso, "1 consulta virou 0" seria
    tecnicamente "-100%" todo dia parado, ruído puro. Só reporta QUEDA
    (um salto de agenda não é um alerta, é uma boa notícia que já aparece
    como número em Agenda & Capacidade, sem precisar virar alerta textual).
    """
    insights: list[Insight] = []
    for weekday in range(7):
        previous_count = previous.weekday_appointment_counts.get(weekday, 0)
        if previous_count < _MIN_WEEKDAY_SAMPLE:
            continue
        current_count = current.weekday_appointment_counts.get(weekday, 0)
        drop_pct = ((previous_count - current_count) / previous_count) * 100
        if drop_pct < _WEEKDAY_DROP_WARNING_PCT:
            continue
        severity = "critical" if drop_pct >= _WEEKDAY_DROP_CRITICAL_PCT else "warning"
        label = _WEEKDAY_LABELS[weekday]
        prefix = "Crítico: " if severity == "critical" else ""
        insights.append(
            Insight(
                severity=severity,
                title=f"Queda de agenda: {label.capitalize()}",
                message=(
                    f"{prefix}A agenda de {label} apresenta uma queda de {drop_pct:.0f}% em relação ao período "
                    f"anterior (de {previous_count} para {current_count} agendamento(s)). É necessário intensificar "
                    "as ações para aumentar os agendamentos do dia."
                ),
            )
        )
    return insights


def _weekday_no_show_rate_insights(current: InsightsPeriodInput) -> list[Insight]:
    """
    Responde diretamente "qual dia da semana tem taxa de falta alta" —
    achado do usuário sobre lacuna do módulo de Agenda (weekday_appointment_counts/
    _weekday_drop_insights só mostravam VOLUME, nunca a taxa). Compara
    cada dia contra a MÉDIA do próprio período (intra-período), não
    contra o período anterior nem contra um corte absoluto — o mesmo
    corte de 30% pode ser trivial pra uma clínica de estética e grave
    pra uma de saúde mental, então "X pontos ACIMA da sua própria média"
    generaliza melhor entre clínicas do que um número fixo.

    Só entram dias com amostra mínima (_MIN_WEEKDAY_SAMPLE) — mesmo
    raciocínio de _weekday_drop_insights: 1 falta em 1 atendimento seria
    "100%", ruído estatístico, não um padrão. Só reporta dias ACIMA da
    média (um dia ótimo não é um alerta, já aparece como número no
    gráfico de apoio de Agenda & Capacidade).
    """
    total_no_show = sum(no_show for no_show, _ in current.weekday_no_show_counts.values())
    total_relevant = sum(total for _, total in current.weekday_no_show_counts.values())
    if total_relevant == 0:
        return []
    overall_rate = total_no_show / total_relevant

    insights: list[Insight] = []
    for weekday in range(7):
        no_show_count, total = current.weekday_no_show_counts.get(weekday, (0, 0))
        if total < _MIN_WEEKDAY_SAMPLE:
            continue
        rate = no_show_count / total
        gap_pp = (rate - overall_rate) * 100
        if gap_pp < _WEEKDAY_NO_SHOW_RATE_WARNING_PP:
            continue
        severity = "critical" if gap_pp >= _WEEKDAY_NO_SHOW_RATE_CRITICAL_PP else "warning"
        label = _WEEKDAY_LABELS[weekday]
        prefix = "Crítico: " if severity == "critical" else ""
        insights.append(
            Insight(
                severity=severity,
                title=f"Taxa de falta acima da média: {label.capitalize()}",
                message=(
                    f"{prefix}{label.capitalize()} tem taxa de falta de {rate * 100:.0f}% ({no_show_count} de "
                    f"{total} atendimento(s) resolvido(s)), {gap_pp:.0f} pontos acima da média do período "
                    f"({overall_rate * 100:.0f}%). Vale reforçar confirmação de presença nesse dia."
                ),
            )
        )
    return insights


def _denial_risk_pct_insight(current: InsightsPeriodInput) -> Insight | None:
    """
    Traduz o backlog de risco de glosa em uma frase de urgência
    financeira em vez de uma contagem seca — segundo exemplo do briefing
    de redesenho ("risco de até 50% de glosas nas contas atuais").
    Baseado em VALOR (R$), não em contagem de linhas: para a diretoria,
    "quanto dinheiro está em risco" é a pergunta real por trás do número.
    """
    if current.denial_risk_pct is None or current.denial_risk_pct < _DENIAL_RISK_PCT_WARNING:
        return None
    severity = "critical" if current.denial_risk_pct >= _DENIAL_RISK_PCT_CRITICAL else "warning"
    prefix = "Alerta de Faturamento: " if severity == "critical" else ""
    return Insight(
        severity=severity,
        title="Risco de glosa no faturamento atual",
        message=(
            f"{prefix}Há um risco de até {current.denial_risk_pct:.0f}% de glosas nas contas faturadas nesta "
            f"janela (R$ {current.denial_at_risk_value:,.2f} em risco médio ou alto). É urgente revisar esses "
            "registros com a equipe de faturamento antes do envio."
        ),
        financial_impact=current.denial_at_risk_value,
    )


def _annual_goal_insight(current: InsightsPeriodInput) -> Insight | None:
    """
    Terceiro exemplo do briefing de redesenho: em vez de um gráfico frio
    de meta, um diagnóstico em texto com recomendação concreta de ação
    (CRM / recuperação de pacientes inativos). Confirmado explicitamente
    pelo usuário: a meta é SEMPRE manual (Tenant.annual_revenue_goal,
    configurada em Minha Clínica) — este motor nunca a calcula sozinho,
    só compara o real com o que foi configurado.

    Compara o faturamento acumulado do ano com o RITMO ESPERADO até
    hoje (meta * fração do ano decorrida), não com a meta inteira — do
    contrário o insight disparia todo santo dia até 31 de dezembro,
    mesmo para uma clínica no ritmo certo.
    """
    if current.annual_revenue_goal is None or current.elapsed_year_fraction is None:
        return None  # sem meta configurada, ou sem contexto de data — não é possível calcular o ritmo esperado
    if current.elapsed_year_fraction <= 0 or current.annual_revenue_goal <= 0:
        return None

    expected_by_now = current.annual_revenue_goal * current.elapsed_year_fraction
    if expected_by_now <= 0 or current.ytd_billed_total >= expected_by_now:
        return None  # no ritmo ou à frente da meta — sem alerta

    behind_pct = ((expected_by_now - current.ytd_billed_total) / expected_by_now) * 100
    if behind_pct < _ANNUAL_GOAL_BEHIND_WARNING_PCT:
        return None  # diferença pequena, dentro do ruído natural de ritmo mês a mês

    severity = "critical" if behind_pct >= _ANNUAL_GOAL_BEHIND_CRITICAL_PCT else "warning"
    progress_pct = (current.ytd_billed_total / current.annual_revenue_goal) * 100
    prefix = "Crítico: " if severity == "critical" else ""
    recovery_note = (
        f" Há {current.inactive_patients_count} paciente(s) sem consulta há mais de 1 ano — "
        "um ponto de partida concreto para essa campanha de recuperação."
        if current.inactive_patients_count > 0
        else ""
    )
    return Insight(
        severity=severity,
        title="Faturamento anual abaixo do ritmo da meta",
        message=(
            f"{prefix}O faturamento anual está em R$ {current.ytd_billed_total:,.2f} ({progress_pct:.0f}% da meta de "
            f"R$ {current.annual_revenue_goal:,.2f}), abaixo do ritmo esperado para esta altura do ano. Recomendamos "
            "iniciar um projeto de CRM para captação de novos clientes ou recuperação de pacientes inativos há mais "
            f"de um ano.{recovery_note}"
        ),
        financial_impact=expected_by_now - current.ytd_billed_total,
    )


def _appeals_due_soon_insight(current: InsightsPeriodInput) -> Insight | None:
    """Diferente dos outros insights (que comparam atual vs. anterior),
    este é puramente um alerta de estado presente — um prazo de recurso
    vencendo não fica "menos urgente" por não ter mudado desde ontem.
    Sempre 'critical': ao contrário de um buraco financeiro (perda que já
    aconteceu), um prazo perdido é uma perda IRREVERSÍVEL e evitável."""
    if current.appeals_due_soon_count <= 0:
        return None
    plural = "s" if current.appeals_due_soon_count != 1 else ""
    return Insight(
        severity="critical",
        title="Prazo de recurso de glosa vencendo",
        message=(
            f"{current.appeals_due_soon_count} recurso{plural} de glosa com prazo vencendo nos próximos dias "
            "(ou já vencido) sem resposta protocolada. Perder o prazo contratual costuma significar perder o "
            "direito de contestar — verifique a lista de recursos em aberto."
        ),
    )


def generate_insights(
    current: InsightsPeriodInput,
    previous: InsightsPeriodInput,
    estimated_no_show_revenue_at_risk: float = 0.0,
    estimated_idle_capacity_revenue_lost: float = 0.0,
) -> list[Insight]:
    insights: list[Insight] = []
    insights.extend(_denial_spike_insights(current, previous))
    insights.extend(_weekday_drop_insights(current, previous))
    insights.extend(_weekday_no_show_rate_insights(current))

    for maybe_insight in (
        _appeals_due_soon_insight(current),
        _denial_risk_pct_insight(current),
        _annual_goal_insight(current),
        _financial_hole_insight(current, previous),
        _payment_gap_insight(current, previous),
        _value_saved_insight(current, previous),
        _capacity_drop_insight(current, previous, estimated_idle_capacity_revenue_lost),
        _no_show_risk_insight(current, estimated_no_show_revenue_at_risk),
    ):
        if maybe_insight is not None:
            insights.append(maybe_insight)

    # Prioriza por impacto financeiro (maior primeiro); alertas sem valor
    # monetário associado (ex: queda de ocupação) ficam depois, ordenados
    # por severidade — crítico antes de atenção antes de positivo.
    severity_rank = {"critical": 0, "warning": 1, "positive": 2}
    insights.sort(key=lambda i: (i.financial_impact is None, -(i.financial_impact or 0), severity_rank[i.severity]))
    return insights
