from datetime import date
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
