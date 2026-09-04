from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel


class PeriodKPI(BaseModel):
    """Um indicador com comparação semana-a-semana — mesmo formato de
    "cartão de terminal financeiro" pedido no briefing: valor atual +
    variação percentual contra o período anterior de mesma duração."""

    value: float
    previous_value: float
    delta_pct: float | None  # None quando previous_value == 0 (variação % indefinida, não é 0%)


class ExecutiveSummaryResponse(BaseModel):
    """GET /api/v1/analytics/executive-summary — Sala de Comando."""

    period_start: date
    period_end: date
    total_billed: PeriodKPI
    total_value_saved: PeriodKPI  # caixa protegido pelo motor anti-glosa
    financial_hole: PeriodKPI  # "Divergência de Cobrança" — cobrado abaixo do contratado
    payment_gap: PeriodKPI  # "Divergência de Recebimento" — pago pela operadora abaixo do contratado (só billings conciliados)
    margin_vs_contracted_pct: float | None  # total_billed / (total_billed + financial_hole) * 100
    avg_capacity_utilization: PeriodKPI | None
    # Backlog ATUAL aguardando revisão humana — não escopado pelo período
    # (mesma decisão de ReportingRepository.billing_summary: é "o que
    # precisa de atenção agora", não "o que aconteceu no período").
    high_risk_pending_count: int
    # Recursos de glosa (ver app/sql/008_denial_appeals.sql) ABERTOS ou
    # PROTOCOLADOS cujo prazo vence dentro da janela de alerta — mesma
    # lógica de "agora", não "no período".
    appeals_due_soon_count: int
    # % do valor faturado no período com denial_risk_level medium/high, e
    # o valor em R$ correspondente (ver AnalyticsService._denial_risk_pct)
    # — alimenta o insight textual "risco de até X% de glosas nas contas
    # atuais" e o número de apoio embaixo dele na Sala de Comando. None
    # quando não há faturamento no período (% sobre base zero é indefinida).
    denial_risk_pct: float | None
    denial_at_risk_value: float


class ProfessionalCapacityMetric(BaseModel):
    professional_id: UUID
    full_name: str
    utilization_rate: float
    no_show_rate: float
    available_minutes: int
    booked_minutes: int
    total_appointments: int


class PeakHourBucket(BaseModel):
    hour: int  # 0-23
    appointment_count: int


class NoShowRiskBucket(BaseModel):
    level: str  # "indeterminado" | "baixo" | "medio" | "alto"
    count: int


class WeekdayBucket(BaseModel):
    weekday: int  # 0=domingo .. 6=sábado — mesma convenção de capacity_service.py
    appointment_count: int


class PatientNoShowRankingItem(BaseModel):
    """Uma linha da "lista vermelha" — ver
    AnalyticsRepository.top_no_show_patients. Só pacientes com pelo menos
    1 falta e amostra mínima no período entram aqui."""

    patient_id: UUID
    full_name: str
    no_show_count: int
    total_appointments: int
    no_show_rate: float


class UpcomingRiskAppointmentItem(BaseModel):
    """Um agendamento futuro com risco médio/alto de falta — ver DECISÃO
    em AnalyticsRepository.upcoming_risk_appointments. Mais próximo
    primeiro; sempre "a partir de agora", não escopado pelo período do
    dashboard."""

    appointment_id: UUID
    patient_full_name: str
    scheduled_at: datetime
    risk_level: str  # "medio" | "alto"


class AgendaMetricsResponse(BaseModel):
    """GET /api/v1/analytics/agenda-metrics — Dashboard da Agenda & Capacidade."""

    period_start: date
    period_end: date
    professionals: list[ProfessionalCapacityMetric]
    peak_hours: list[PeakHourBucket]
    # Gráfico de apoio (número/evidência) do insight textual de queda de
    # agenda por dia da semana — ver smart_insights_engine.py::_weekday_drop_insights.
    weekday_histogram: list[WeekdayBucket]
    no_show_risk_breakdown: list[NoShowRiskBucket]
    # Estimativa: contagem de agendamentos futuros com risco ALTO de
    # falta × valor médio cobrado no período — ver DECISÃO em
    # AnalyticsService.get_agenda_metrics sobre por que é uma aproximação,
    # não um número contábil fechado.
    estimated_revenue_at_risk: float
    # "Lista vermelha" — ranking de pacientes por taxa de falta no
    # período, ordenado do pior para o melhor (ver
    # AnalyticsRepository.top_no_show_patients). Lista vazia é o caso
    # feliz (ninguém bateu a amostra mínima com pelo menos 1 falta).
    patient_no_show_ranking: list[PatientNoShowRankingItem]
    # Lista nominal de próximos agendamentos em risco (ver
    # AnalyticsRepository.upcoming_risk_appointments) — alimenta o card
    # "Risco de falta — próximos dias" da Sala de Comando.
    upcoming_risk_appointments: list[UpcomingRiskAppointmentItem]
    # Minutos disponíveis (grade semanal) MENOS minutos agendados, somado
    # entre profissionais com grade cadastrada — o "outro lado" do
    # problema de agenda em relação ao no-show: não "paciente faltou", e
    # sim "nem tinha agendamento marcado nesse horário". Profissional sem
    # grade cadastrada não entra na conta (ver DECISÃO em
    # AnalyticsService._idle_capacity_totals).
    total_idle_minutes: int
    # Tradução em R$ de total_idle_minutes — ver DECISÃO completa em
    # capacity_service.estimate_idle_capacity_revenue_lost. Mesma
    # natureza de estimativa que estimated_revenue_at_risk (acima): não é
    # número contábil fechado, é o ritmo observado projetado sobre o
    # tempo vazio.
    estimated_revenue_lost_to_idle_capacity: float


class PlanLossItem(BaseModel):
    """Uma linha do ranking de perda financeira por convênio — as TRÊS
    fontes de perda já existentes no sistema (buraco de cobrança,
    divergência de recebimento, valor faturado em risco de glosa),
    somadas por operadora em vez de um único número do tenant inteiro.
    Os três componentes continuam expostos separados (nunca só o total)
    pelo mesmo motivo de financial_hole/payment_gap nunca serem somados
    silenciosamente em ExecutiveSummaryResponse: são perdas de natureza
    diferente, e quem decide o que fazer precisa saber qual delas pesa
    mais em cada convênio."""

    plan_name: str
    financial_hole: float  # cobrado abaixo do contratado
    payment_gap: float  # pago pela operadora abaixo do contratado (só billings conciliados)
    denial_risk_value: float  # valor faturado com risco de glosa médio/alto
    total_loss: float  # soma dos três — só para ordenar o ranking


class PlanLossRankingResponse(BaseModel):
    """GET /api/v1/analytics/plan-loss-ranking — Painel → Faturamento."""

    period_start: date
    period_end: date
    plans: list[PlanLossItem]  # ordenado por total_loss, maior perda primeiro


class ContractUtilizationItem(BaseModel):
    """Uma linha do ranking de utilização de contrato — ver DECISÃO em
    AnalyticsRepository.contract_utilization sobre por que
    idle_catalog_value é valor de TABELA dos itens parados, não uma
    estimativa de receita perdida."""

    contract_id: UUID
    plan_name: str
    valid_from: date
    valid_until: date | None
    total_items: int  # procedimentos negociados no contrato
    items_billed: int  # quantos desses foram faturados ao menos 1x no período
    utilization_pct: float  # items_billed / total_items * 100
    idle_catalog_value: float  # valor de tabela dos itens NUNCA faturados no período


class ContractUtilizationResponse(BaseModel):
    """GET /api/v1/analytics/contract-utilization — Painel → Faturamento."""

    period_start: date
    period_end: date
    contracts: list[ContractUtilizationItem]  # ordenado por utilization_pct, pior primeiro


class DenialRiskDistributionItem(BaseModel):
    level: str  # "low" | "medium" | "high"
    count: int


class DenialRiskDistributionResponse(BaseModel):
    """GET /api/v1/analytics/denial-risk-distribution — Painel → Faturamento
    (donut "Distribuição de risco de glosa" do canvas de design)."""

    period_start: date
    period_end: date
    items: list[DenialRiskDistributionItem]
    # Soma dos 3 níveis — ver DECISÃO em
    # AnalyticsRepository.denial_risk_count_breakdown sobre por que
    # "revisado" é sinônimo de "faturado no período" neste produto.
    total_reviewed: int


class SmartInsightResponse(BaseModel):
    severity: str  # "critical" | "warning" | "positive"
    title: str
    message: str
    financial_impact: float | None


class SmartInsightsResponse(BaseModel):
    """GET /api/v1/analytics/smart-insights — Painel de Insights."""

    period_start: date
    period_end: date
    insights: list[SmartInsightResponse]
