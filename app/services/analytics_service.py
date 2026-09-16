"""
app/services/analytics_service.py

Orquestra os Dashboards de Decisão: reaproveita CapacityService (já
existente, ver capacity_service.py), ReportingRepository (billing_summary/
no_show_count, já usados no relatório semanal) e o AnalyticsRepository
novo desta sprint — nenhum cálculo de negócio novo é inventado aqui além
do que já existia espalhado nos outros módulos, mesmo princípio de
ReportDataService.

DECISÃO — comparação semana-a-semana é sempre "período anterior de mesma
duração", não necessariamente 7 dias
-------------------------------------------------------------------------
O briefing pede "variação percentual semanal" nos cartões, mas o usuário
pode filtrar qualquer intervalo (ex: mês inteiro). Comparar sempre contra
os N dias imediatamente anteriores ao período pedido (N = duração do
período atual) generaliza a mesma ideia sem assumir semana fixa — se o
usuário pedir 7 dias, o resultado JÁ é "semana vs. semana anterior".
"""
import calendar
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.core.text_utils import slugify
from app.repositories.analytics_repository import AnalyticsRepository
from app.repositories.capacity_repository import CapacityRepository
from app.repositories.contract_repository import ContractRepository
from app.repositories.cost_entry_repository import CostEntryRepository
from app.repositories.denial_appeal_repository import DenialAppealRepository
from app.repositories.health_score_snapshot_repository import HealthScoreSnapshotRepository
from app.repositories.insight_outcome_repository import InsightOutcomeRepository
from app.repositories.lote_repository import LoteRepository
from app.repositories.professional_availability_repository import ProfessionalAvailabilityRepository
from app.repositories.professional_repository import ProfessionalRepository
from app.repositories.reporting_repository import ReportingRepository
from app.repositories.tenant_repository import TenantRepository
from app.schemas.analytics import (
    AgendaMetricsResponse,
    AgendaRevenueForecastResponse,
    ContractUtilizationItem,
    ContractUtilizationResponse,
    DataQualityByUserItem,
    DataQualityResponse,
    DenialReasonConfirmationItem,
    DenialReasonConfirmationResponse,
    DenialRiskDistributionItem,
    DenialRiskDistributionResponse,
    EarlyChurnRiskItem,
    EarlyChurnRiskResponse,
    ExecutiveSummaryResponse,
    MarketingChannelItem,
    MarketingChannelsResponse,
    ProcedureProfitabilityItem,
    ProfessionalProfitabilityItem,
    ProfitabilityResponse,
    HealthScoreComponentResponse,
    HealthScoreResponse,
    HealthScoreTrendResponse,
    InactivePatientItem,
    InactivePatientsResponse,
    NoShowRiskBucket,
    PatientNoShowRankingItem,
    PeakHourBucket,
    PeriodKPI,
    FinancialHoleBillingItem,
    FinancialHoleBillingsResponse,
    PaymentLagByPlanItem,
    PaymentLagByPlanResponse,
    PlanLossItem,
    PlanLossRankingResponse,
    PriorityQueueItem,
    PriorityQueueResponse,
    CapitalDecisionBaseDataResponse,
    ProductRoiResponse,
    ProfessionalCapacityMetric,
    RecallCandidateItem,
    RecallCandidatesResponse,
    SatisfactionSummaryResponse,
    SmartInsightResponse,
    SmartInsightsResponse,
    UpcomingRiskAppointmentItem,
    WeekdayBucket,
    WeekdayNoShowRateBucket,
)
from app.services.capacity_service import CapacityService, estimate_idle_capacity_revenue_lost
from app.services.health_score_engine import (
    compute_health_score,
    resolve_health_score_ceilings,
    resolve_no_show_ceiling_for_period,
    suggest_denial_rate_ceiling,
    suggest_no_show_rate_ceiling,
)
from app.services.smart_insights_engine import (
    DenialReasonCount,
    InsightsPeriodInput,
    build_network_comparativo_insight,
    describe_denial_reason,
    describe_worst_no_show_weekday,
    generate_insights,
    is_true_denial_risk_reason,
    resolve_denial_risk_thresholds,
    suggest_denial_risk_thresholds,
)

# Amostra mínima antes de reportar a taxa de confirmação de um motivo
# (ver AnalyticsService.get_denial_reason_confirmation) — mesmo valor
# default de AnalyticsRepository.professional_denial_rates, mesmo
# raciocínio de MIN_SPECIFIC_SAMPLES (no_show_risk_engine.py): um
# "chute" de partida documentado, não uma calibração validada com dado
# real (mesma limitação já registrada no Achado 7 da Auditoria para os
# demais limiares deste produto).
DENIAL_REASON_CONFIRMATION_MIN_SAMPLE = 5

# Épico F2.2 do Plano Diretor ("Qualidade de dado na origem") — amostra
# mínima de atendimentos lançados por um atendente antes de reportar sua
# taxa de completude, mesmo raciocínio de DENIAL_REASON_CONFIRMATION_MIN_SAMPLE
# logo acima: 1-2 lançamentos não é um "padrão do atendente", é ruído.
DATA_QUALITY_MIN_SAMPLE = 5

# Janela FIXA da Nota de Saúde Financeira — de propósito independente do
# seletor de período da Sala de Comando (que pode ser 7 dias). Um score
# de "tendência de saúde da clínica" que pula toda vez que o usuário
# muda o filtro de 7 para 30 dias pareceria ruído, não sinal — e 90 dias
# dá amostra mínima razoável para o componente de recurso de glosa
# (resolução de recurso é lenta, poucos por semana).
_HEALTH_SCORE_WINDOW_DAYS = 90
# "Equilíbrio Insighta" (Balanced Scorecard, perna Cliente) — mesma
# janela fixa de 90 dias da Nota de Saúde Financeira, mesmo raciocínio:
# indicador de tendência, não retrato de um dia só.
_SATISFACTION_WINDOW_DAYS = 90

# Referência da tendência do anel de saúde: "como eu estava há 3 meses"
# — mesma janela de 90 dias, por consistência com a própria nota (que já
# usa essa janela para o cálculo atual). Ver DECISÃO completa em
# app/sql/034_health_score_snapshots.sql.
_HEALTH_SCORE_TREND_REFERENCE_DAYS = 90

# Mesmo piso usado por _annual_goal_insight (via inactive_patients_count)
# — "não volta há mais de 1 ano" — repetido aqui só como nome, não como
# valor duplicado de propósito: os dois pontos que citam esse número (o
# insight de meta anual e a lista de get_inactive_patients) precisam
# sempre bater no mesmo piso.
_INACTIVE_PATIENT_AFTER_DAYS = 365

# Raio-X da Receita, frente "Prevendo movimentos" — risco de abandono
# ANTECIPADO (ver AnalyticsRepository.list_early_churn_risk_patients e
# get_early_churn_risk acima). 3 consultas é o mesmo piso de amostra
# mínima documentado em MIN_SPECIFIC_SAMPLES (no_show_risk_engine.py) —
# com menos, "intervalo médio entre consultas" é estatisticamente vazio.
# 2x é um "chute razoável" de v1 (mesma limitação de sempre): dobrar o
# próprio ritmo sem voltar já é um desvio grande o bastante pra não ser
# ruído normal de agenda.
_EARLY_CHURN_MIN_VISITS = 3
_EARLY_CHURN_GAP_MULTIPLIER = 2.0

# Janela de alerta de prazo de recurso: "vencendo em breve" — mesmo
# princípio de MIN_SAMPLE_SIZE/thresholds em smart_insights_engine.py,
# um número fixo e nomeado em vez de mágico espalhado pelo código.
APPEAL_DEADLINE_ALERT_HORIZON_DAYS = 5

# "O que resta em aberto" da Auditoria de Templates e Insights: peça
# natural do mesmo padrão que Guia/coparticipação já fecharam —
# core.lotes.status/closed_at (Fase 2) já modelados, sem nenhum insight
# consumindo até esta rodada. Mesmo motivo de o corte viver AQUI (e não
# em smart_insights_engine.py, junto dos outros limiares) que
# APPEAL_DEADLINE_ALERT_HORIZON_DAYS acima: o corte precisa chegar até a
# query SQL (LoteRepository.stale_open_lotes_summary), não é aplicado
# sobre um dado já bruto que o motor filtra depois — o motor
# (_stale_open_lotes_insight) só decide "mostra ou não", já recebe a
# contagem pronta. 30 dias é um chute razoável (ciclo de fechamento
# mensal de lote é comum no mercado — mesmos 3 ERPs pesquisados pra
# Guia/Lote), não calibrado com dado real — mesma limitação já
# documentada no Achado 7 da Auditoria para os demais limiares deste
# motor: revisitar quando houver volume real de uso.
_STALE_LOTE_AFTER_DAYS = 30

# Raio-X da Receita, frente "Evitando perdas" — Contract.valid_until
# sempre existiu no banco, sem nenhum insight avisando ANTES do
# vencimento (ver ContractRepository.expiring_without_renewal_summary e
# smart_insights_engine.py::_contract_expiring_insight). Mesmo motivo de
# o corte viver AQUI (não no motor) que _STALE_LOTE_AFTER_DAYS acima: a
# janela precisa chegar até a query SQL. 30 dias é o mesmo "chute
# razoável" documentado nos demais limiares deste produto.
CONTRACT_EXPIRING_ALERT_HORIZON_DAYS = 30

# "Lista vermelha" de pacientes (Painel → Agenda) — mesmo raciocínio de
# amostra mínima de no_show_risk_engine.MIN_SPECIFIC_SAMPLES: exige pelo
# menos 3 atendimentos no período para uma taxa de falta significar
# alguma coisa, e mostra só os 10 piores para a lista continuar
# acionável (uma tabela de 200 pacientes não é uma "lista vermelha", é
# ruído de novo).
RED_LIST_MIN_SAMPLE = 3
RED_LIST_LIMIT = 10


@dataclass
class _PeriodRange:
    start: date
    end: date


@dataclass
class _AnnualGoalContext:
    """Insumos do insight de meta anual (ver smart_insights_engine.py::
    _annual_goal_insight) — calculados uma única vez em get_smart_insights
    e injetados só no InsightsPeriodInput do período ATUAL (não faz
    sentido "meta anual do período anterior", é um estado presente)."""

    annual_revenue_goal: float | None
    elapsed_year_fraction: float
    ytd_billed_total: float
    inactive_patients_count: int


def _previous_period(date_from: date, date_to: date) -> _PeriodRange:
    duration_days = (date_to - date_from).days + 1
    previous_end = date_from - timedelta(days=1)
    previous_start = previous_end - timedelta(days=duration_days - 1)
    return _PeriodRange(previous_start, previous_end)


def _year_ago_period(date_from: date, date_to: date) -> _PeriodRange:
    """Raio-X da Receita, frente "Prevendo movimentos" — mesma janela,
    exatamente 364 dias antes (52 semanas, não 1 ano de calendário).
    Preserva o dia da semana de cada data (uma segunda-feira continua
    caindo numa segunda-feira um ano antes) — importa numa clínica com
    padrão semanal forte (ver _weekday_drop_insight): subtrair 1 ano de
    calendário (365 ou 366 dias) deslocaria o dia da semana em 1-2 dias,
    comparando a segunda-feira de hoje com uma terça-feira do ano
    passado, uma comparação sutilmente errada."""
    return _PeriodRange(date_from - timedelta(days=364), date_to - timedelta(days=364))


def _delta_pct(current: float, previous: float) -> float | None:
    # Mesma lógica de compute_roi_pct em report_calculations.py: variação
    # percentual contra base ZERO é indefinida, não "infinita" nem "0%" —
    # reportar um número aqui seria uma afirmação numérica falsa.
    if previous == 0:
        return None
    return ((current - previous) / previous) * 100


def _elapsed_year_fraction(as_of: date) -> float:
    """Fração do ano CALENDÁRIO já decorrida até `as_of` (inclusive) —
    alimenta o insight de meta anual (ver smart_insights_engine.py::
    _annual_goal_insight). Calculado aqui (não no motor) porque depende
    de "hoje", e o motor precisa continuar puro/testável com datas
    fixas. Usa a duração REAL do ano (365 ou 366 dias) em vez de 365 fixo,
    para não distorcer o ritmo esperado em anos bissextos."""
    year_start = date(as_of.year, 1, 1)
    next_year_start = date(as_of.year + 1, 1, 1)
    days_in_year = (next_year_start - year_start).days
    elapsed_days = (as_of - year_start).days + 1
    return min(elapsed_days / days_in_year, 1.0)


def _denial_risk_pct(risk_value_breakdown: dict[str, float]) -> tuple[float | None, float]:
    """(percentual, valor em R$) do total faturado com denial_risk_level
    medium/high — usado tanto pelo KPI "Faturamento retido" (Sala de
    Comando) quanto pelo insight textual de risco de glosa (ver
    smart_insights_engine.py::_denial_risk_pct_insight). Percentual sobre
    base zero é None, mesmo princípio de _delta_pct — sem faturamento no
    período, "risco de glosa" não tem denominador para significar nada."""
    total = sum(risk_value_breakdown.values())
    at_risk = risk_value_breakdown.get("medium", 0.0) + risk_value_breakdown.get("high", 0.0)
    if total <= 0:
        return None, 0.0
    return (at_risk / total) * 100, at_risk


# Épico F2.1 do Plano Diretor ("Calibração por especialidade/porte") —
# quantos meses FECHADOS de histórico buscar para sugerir um limiar
# calibrado pela própria clínica (ver threshold_calibration.py). "Mês
# fechado" exclui o mês corrente (ainda parcial) de propósito — um mês
# com só 5 dias faturados teria uma taxa artificialmente instável. 12
# meses é generoso o bastante para cobrir sazonalidade sem custar muitas
# queries (cada mês é 1-2 SELECTs pequenos, reaproveitando repositório já
# existente — não uma SQL nova agregando por mês, já que este cálculo só
# roda quando alguém pede a sugestão, nunca num dashboard de alta
# frequência).
_THRESHOLD_SUGGESTION_LOOKBACK_MONTHS = 12


def _preceding_month_bounds(months_back: int) -> tuple[date, date]:
    """(primeiro dia, último dia) do mês `months_back` meses atrás do mês
    CORRENTE — months_back=1 é o mês passado (o mais recente FECHADO),
    nunca o mês corrente em si."""
    today = date.today()
    year = today.year
    month = today.month - months_back
    while month <= 0:
        month += 12
        year -= 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


async def monthly_denial_risk_pcts(
    analytics_repo: AnalyticsRepository, *, months: int = _THRESHOLD_SUGGESTION_LOOKBACK_MONTHS
) -> list[float]:
    """Série de denial_risk_pct (escala 0-100) dos últimos `months` meses
    FECHADOS desta clínica — insumo de
    smart_insights_engine.suggest_denial_risk_thresholds. Só entram meses
    com faturamento no período (mesmo critério de _denial_risk_pct: sem
    base, o mês não tem uma taxa real para contribuir — não é 0%, é
    ausência de amostra)."""
    pcts: list[float] = []
    for months_back in range(1, months + 1):
        start, end = _preceding_month_bounds(months_back)
        breakdown = await analytics_repo.denial_risk_value_breakdown(start, end)
        pct, _value = _denial_risk_pct(breakdown)
        if pct is not None:
            pcts.append(pct)
    return pcts


async def monthly_no_show_rates(
    analytics_repo: AnalyticsRepository, *, months: int = _THRESHOLD_SUGGESTION_LOOKBACK_MONTHS
) -> list[float]:
    """Série de taxa de falta (fração 0-1) dos últimos `months` meses
    FECHADOS desta clínica — insumo de
    health_score_engine.suggest_no_show_rate_ceiling. Só entram meses com
    pelo menos 1 atendimento resolvido (completed/no_show) — mesmo
    critério de "sem amostra != 0%" do resto do produto."""
    rates: list[float] = []
    for months_back in range(1, months + 1):
        start, end = _preceding_month_bounds(months_back)
        no_show_count, total = await analytics_repo.overall_no_show_rate(start, end)
        if total > 0:
            rates.append(no_show_count / total)
    return rates


async def monthly_no_show_rates_by_specialty(
    analytics_repo: AnalyticsRepository, *, months: int = _THRESHOLD_SUGGESTION_LOOKBACK_MONTHS
) -> dict[str, list[float]]:
    """Mesma série de `monthly_no_show_rates` acima, quebrada por
    especialidade do profissional — "Junta Técnica Insighta": insumo de
    health_score_engine.resolve_no_show_ceiling_for_period (calibração
    por especialidade DENTRO do próprio histórico da clínica, nunca um
    benchmark externo — ver DECISÃO em threshold_calibration.py). Só
    entram meses com pelo menos 1 atendimento resolvido NAQUELA
    especialidade — mesmo critério "sem amostra != 0%" de sempre."""
    rates_by_specialty: dict[str, list[float]] = {}
    for months_back in range(1, months + 1):
        start, end = _preceding_month_bounds(months_back)
        counts = await analytics_repo.no_show_rate_by_specialty(start, end)
        for specialty, (no_show_count, total) in counts.items():
            if total > 0:
                rates_by_specialty.setdefault(specialty, []).append(no_show_count / total)
    return rates_by_specialty


def _regroup_text_counts(breakdown: dict[str, int]) -> dict[str, int]:
    """
    Achado 3 da Auditoria de Templates e Insights (alto) — `canal_agendamento`/
    `motivo_cancelamento` são texto livre por decisão documentada (ver
    RawAppointmentRow, app/worker/schemas.py), sem vocabulário fechado.
    `AnalyticsRepository.cancellation_reason_breakdown` já agrupa por
    valor EXATO da coluna no SQL — "WhatsApp", "whatsapp" e "Whats App"
    do MESMO cliente contariam como 3 motivos/canais diferentes, cada um
    com amostra menor, escondendo o padrão real em vez de revelá-lo.

    Reagrupa aqui, em Python, por `slugify()` — a MESMA normalização já
    usada para casar nome de convênio (ver InsurancePlanRepository.resolve)
    — somando as contagens de cada grafia equivalente e usando a grafia
    de MAIOR contagem como rótulo de exibição (nunca inventa um rótulo
    novo, só escolhe entre os que o próprio dado trouxe). Feito em
    Python, não em SQL: o número de motivos/canais DISTINTOS que chega
    até aqui já é pequeno (a agregação pesada — por linha de atendimento
    — already aconteceu no banco), então não há custo de performance em
    reagrupar um punhado de chaves em memória.
    """
    totals: dict[str, int] = {}
    best_label_count: dict[str, int] = {}
    best_label: dict[str, str] = {}
    for raw_label, count in breakdown.items():
        slug = slugify(raw_label) or raw_label
        totals[slug] = totals.get(slug, 0) + count
        if count > best_label_count.get(slug, -1):
            best_label_count[slug] = count
            best_label[slug] = raw_label
    return {best_label[slug]: total for slug, total in totals.items()}


def _regroup_text_no_show_counts(breakdown: dict[str, tuple[int, int]]) -> dict[str, tuple[int, int]]:
    """Equivalente a `_regroup_text_counts`, mas para
    `AnalyticsRepository.booking_channel_no_show_rate_breakdown`, cujo
    valor é (no_show_count, total_relevante) em vez de uma contagem
    única — soma os DOIS lados do par por grafia equivalente, mesma
    DECISÃO de `_regroup_text_counts` acima."""
    totals: dict[str, tuple[int, int]] = {}
    best_label_total: dict[str, int] = {}
    best_label: dict[str, str] = {}
    for raw_label, (no_show, total) in breakdown.items():
        slug = slugify(raw_label) or raw_label
        prev_no_show, prev_total = totals.get(slug, (0, 0))
        totals[slug] = (prev_no_show + no_show, prev_total + total)
        if total > best_label_total.get(slug, -1):
            best_label_total[slug] = total
            best_label[slug] = raw_label
    return {best_label[slug]: counts for slug, counts in totals.items()}


class AnalyticsService:
    def __init__(
        self,
        analytics_repo: AnalyticsRepository,
        reporting_repo: ReportingRepository,
        professional_repo: ProfessionalRepository,
        availability_repo: ProfessionalAvailabilityRepository,
        capacity_repo: CapacityRepository,
        appeal_repo: DenialAppealRepository,
        tenant_repo: TenantRepository,
        health_score_snapshot_repo: HealthScoreSnapshotRepository,
        lote_repo: LoteRepository,
        contract_repo: ContractRepository,
        cost_entry_repo: CostEntryRepository,
        insight_outcome_repo: InsightOutcomeRepository,
    ):
        self.analytics_repo = analytics_repo
        self.reporting_repo = reporting_repo
        self.professional_repo = professional_repo
        self.appeal_repo = appeal_repo
        self.tenant_repo = tenant_repo
        self.availability_repo = availability_repo
        self.health_score_snapshot_repo = health_score_snapshot_repo
        self.lote_repo = lote_repo
        self.contract_repo = contract_repo
        self.cost_entry_repo = cost_entry_repo
        self.insight_outcome_repo = insight_outcome_repo
        self.capacity_service = CapacityService(availability_repo, capacity_repo)

    async def _avg_utilization(self, date_from: date, date_to: date) -> float | None:
        professionals = await self.professional_repo.list_active()
        rates = []
        for professional in professionals:
            result = await self.capacity_service.get_utilization(professional.id, date_from, date_to)
            if result.available_minutes > 0:
                rates.append(result.utilization_rate)
        if not rates:
            return None
        return sum(rates) / len(rates)

    async def _idle_capacity_totals(self, date_from: date, date_to: date) -> tuple[int, int, int]:
        """(idle_minutes, booked_minutes, total_appointments) somados
        entre profissionais com grade cadastrada — insumo de
        capacity_service.estimate_idle_capacity_revenue_lost. Profissional
        sem grade (available_minutes == 0) não entra: sem capacidade
        teórica instalada, não há "ocioso" para medir nele, mesmo
        critério de _avg_utilization acima."""
        professionals = await self.professional_repo.list_active()
        idle_minutes = 0
        booked_minutes = 0
        total_appointments = 0
        for professional in professionals:
            result = await self.capacity_service.get_utilization(professional.id, date_from, date_to)
            if result.available_minutes <= 0:
                continue
            idle_minutes += max(result.available_minutes - result.booked_minutes, 0)
            booked_minutes += result.booked_minutes
            total_appointments += result.total_appointments
        return idle_minutes, booked_minutes, total_appointments

    async def get_executive_summary(self, date_from: date, date_to: date) -> ExecutiveSummaryResponse:
        previous = _previous_period(date_from, date_to)

        current_billing = await self.reporting_repo.billing_summary(date_from, date_to)
        previous_billing = await self.reporting_repo.billing_summary(previous.start, previous.end)

        current_hole = await self.analytics_repo.financial_hole_total(date_from, date_to)
        previous_hole = await self.analytics_repo.financial_hole_total(previous.start, previous.end)

        current_gap = await self.analytics_repo.payment_gap_total(date_from, date_to)
        previous_gap = await self.analytics_repo.payment_gap_total(previous.start, previous.end)

        current_utilization = await self._avg_utilization(date_from, date_to)
        previous_utilization = await self._avg_utilization(previous.start, previous.end)

        # PMR (achado da auditoria "Veredito do Gestor Clínico") — ver
        # DECISÃO completa em AnalyticsRepository.payment_lag_total.
        current_lag_days, _current_lag_count = await self.analytics_repo.payment_lag_total(date_from, date_to)
        previous_lag_days, _previous_lag_count = await self.analytics_repo.payment_lag_total(previous.start, previous.end)

        appeals_due_soon_count = await self.appeal_repo.count_due_within(
            as_of=date.today(), horizon_days=APPEAL_DEADLINE_ALERT_HORIZON_DAYS
        )

        current_risk_value_breakdown = await self.analytics_repo.denial_risk_value_breakdown(date_from, date_to)
        denial_risk_pct, denial_at_risk_value = _denial_risk_pct(current_risk_value_breakdown)

        expected_value = current_billing["total_billed"] + current_hole
        margin_vs_contracted_pct = (
            (current_billing["total_billed"] / expected_value) * 100 if expected_value > 0 else None
        )

        return ExecutiveSummaryResponse(
            period_start=date_from,
            period_end=date_to,
            total_billed=PeriodKPI(
                value=current_billing["total_billed"],
                previous_value=previous_billing["total_billed"],
                delta_pct=_delta_pct(current_billing["total_billed"], previous_billing["total_billed"]),
            ),
            total_value_saved=PeriodKPI(
                value=current_billing["total_value_saved"],
                previous_value=previous_billing["total_value_saved"],
                delta_pct=_delta_pct(current_billing["total_value_saved"], previous_billing["total_value_saved"]),
            ),
            financial_hole=PeriodKPI(
                value=current_hole,
                previous_value=previous_hole,
                delta_pct=_delta_pct(current_hole, previous_hole),
            ),
            payment_gap=PeriodKPI(
                value=current_gap,
                previous_value=previous_gap,
                delta_pct=_delta_pct(current_gap, previous_gap),
            ),
            margin_vs_contracted_pct=margin_vs_contracted_pct,
            avg_capacity_utilization=(
                PeriodKPI(
                    value=current_utilization,
                    previous_value=previous_utilization or 0.0,
                    delta_pct=_delta_pct(current_utilization, previous_utilization or 0.0),
                )
                if current_utilization is not None
                else None
            ),
            high_risk_pending_count=current_billing["high_risk_pending_count"],
            appeals_due_soon_count=appeals_due_soon_count,
            denial_risk_pct=denial_risk_pct,
            denial_at_risk_value=denial_at_risk_value,
            avg_days_to_receive=(
                PeriodKPI(
                    value=current_lag_days,
                    previous_value=previous_lag_days or 0.0,
                    delta_pct=_delta_pct(current_lag_days, previous_lag_days or 0.0),
                )
                if current_lag_days is not None
                else None
            ),
        )

    async def get_payment_lag_by_plan(self, date_from: date, date_to: date) -> PaymentLagByPlanResponse:
        """
        Ranking de PMR por convênio — pior prazo primeiro, pra apontar
        QUAL operadora está de fato travando o caixa (ver DECISÃO
        completa em AnalyticsRepository.payment_lag_total/
        payment_lag_by_plan e "Veredito do Gestor Clínico", achado 2).
        `avg_days_to_receive`/`billings_settled_count` no topo repetem o
        agregado do tenant inteiro (mesmo número de
        ExecutiveSummaryResponse.avg_days_to_receive.value) — os `items`
        decompõem isso por convênio.
        """
        avg_days, settled_count = await self.analytics_repo.payment_lag_total(date_from, date_to)
        rows = await self.analytics_repo.payment_lag_by_plan(date_from, date_to)
        return PaymentLagByPlanResponse(
            period_start=date_from,
            period_end=date_to,
            avg_days_to_receive=avg_days,
            billings_settled_count=settled_count,
            items=[
                PaymentLagByPlanItem(
                    insurance_plan_id=uuid.UUID(row["insurance_plan_id"]),
                    insurance_plan_name=row["insurance_plan_name"],
                    avg_days_to_receive=row["avg_days_to_receive"],
                    billings_settled_count=row["billings_settled_count"],
                )
                for row in rows
            ],
        )

    async def get_agenda_metrics(self, date_from: date, date_to: date) -> AgendaMetricsResponse:
        professionals = await self.professional_repo.list_active()
        professional_metrics = []
        for professional in professionals:
            result = await self.capacity_service.get_utilization(professional.id, date_from, date_to)
            professional_metrics.append(
                ProfessionalCapacityMetric(
                    professional_id=professional.id,
                    full_name=professional.full_name,
                    utilization_rate=result.utilization_rate,
                    no_show_rate=result.no_show_rate,
                    available_minutes=result.available_minutes,
                    booked_minutes=result.booked_minutes,
                    total_appointments=result.total_appointments,
                )
            )

        hour_histogram = await self.analytics_repo.appointment_hour_histogram(date_from, date_to)
        peak_hours = [
            PeakHourBucket(hour=hour, appointment_count=count)
            for hour, count in sorted(hour_histogram.items(), key=lambda item: item[0])
        ]

        risk_breakdown = await self.analytics_repo.no_show_risk_breakdown(as_of=datetime.now(timezone.utc))
        avg_charged = await self.analytics_repo.avg_charged_value(date_from, date_to)
        # DECISÃO — estimativa, não número contábil fechado: multiplica o
        # volume de agendamentos com risco ALTO pelo valor médio cobrado
        # no período. Não sabemos o valor exato de uma consulta que ainda
        # nem aconteceu (billing só existe depois do atendimento) — mesma
        # simplificação deliberada documentada para ROI de marketing em
        # reporting_repository.py.
        high_risk_count = risk_breakdown.get("alto", 0)
        estimated_revenue_at_risk = high_risk_count * avg_charged

        # Gráfico de apoio do insight textual "a agenda de segunda caiu
        # X%" (ver smart_insights_engine.py::_weekday_drop_insights) —
        # número em evidência ABAIXO do diagnóstico em texto, não o
        # elemento principal da tela (ver redesenho da Sala de Comando).
        weekday_histogram = await self.analytics_repo.appointment_weekday_histogram(date_from, date_to)
        weekday_buckets = [
            WeekdayBucket(weekday=weekday, appointment_count=count)
            for weekday, count in sorted(weekday_histogram.items(), key=lambda item: item[0])
        ]

        # Taxa de falta por dia da semana — não confundir com
        # weekday_buckets acima (volume). Alimenta o mesmo gráfico de
        # apoio, agora para o insight textual "quinta tem taxa de falta
        # X%" (ver smart_insights_engine.py::_weekday_no_show_rate_insights).
        weekday_no_show_counts = await self.analytics_repo.weekday_no_show_rate_breakdown(date_from, date_to)
        weekday_no_show_buckets = [
            WeekdayNoShowRateBucket(
                weekday=weekday,
                no_show_count=no_show,
                total_appointments=total,
                no_show_rate=(no_show / total) if total > 0 else None,
            )
            for weekday, (no_show, total) in sorted(weekday_no_show_counts.items(), key=lambda item: item[0])
        ]

        # Quantos profissionais ativos ainda não têm NENHUM bloco de grade
        # cadastrado — checagem INDEPENDENTE da janela de período pedida
        # de propósito: `available_minutes <= 0` (usado por
        # _idle_capacity_totals para "sem capacidade instalada, exclui da
        # conta") also é 0 para um profissional que TEM grade mas ela só
        # cai em dias fora da janela filtrada (ex: só atende terça, e o
        # dashboard está filtrado numa semana sem nenhuma terça) — usar
        # esse sinal aqui contaria falso positivo. Este número existe pra
        # essa lacuna parar de ser silenciosa (ver DECISÃO no schema) —
        # todo profissional auto-criado por upload de arquivo (Faturamento
        # OU Agenda) nasce sem grade nenhuma, em qualquer janela.
        availability_by_professional = await self.availability_repo.list_by_professionals([p.id for p in professionals])
        professionals_without_availability_count = sum(
            1 for blocks in availability_by_professional.values() if not blocks
        )

        red_list = await self.analytics_repo.top_no_show_patients(
            date_from, date_to, min_sample=RED_LIST_MIN_SAMPLE, limit=RED_LIST_LIMIT
        )

        upcoming_risk = await self.analytics_repo.upcoming_risk_appointments(as_of=datetime.now(timezone.utc))

        idle_minutes, idle_booked_minutes, idle_total_appointments = await self._idle_capacity_totals(date_from, date_to)
        estimated_revenue_lost_to_idle_capacity = estimate_idle_capacity_revenue_lost(
            idle_minutes=idle_minutes,
            booked_minutes=idle_booked_minutes,
            total_appointments=idle_total_appointments,
            avg_charged_value=avg_charged,
        )

        return AgendaMetricsResponse(
            period_start=date_from,
            period_end=date_to,
            professionals=professional_metrics,
            peak_hours=peak_hours,
            weekday_histogram=weekday_buckets,
            weekday_no_show_rates=weekday_no_show_buckets,
            no_show_risk_breakdown=[NoShowRiskBucket(level=level, count=count) for level, count in risk_breakdown.items()],
            estimated_revenue_at_risk=estimated_revenue_at_risk,
            patient_no_show_ranking=[
                PatientNoShowRankingItem(
                    patient_id=row["patient_id"],
                    full_name=row["full_name"],
                    no_show_count=row["no_show_count"],
                    total_appointments=row["total_appointments"],
                    no_show_rate=row["no_show_rate"],
                )
                for row in red_list
            ],
            upcoming_risk_appointments=[
                UpcomingRiskAppointmentItem(
                    appointment_id=row["appointment_id"],
                    patient_full_name=row["patient_full_name"],
                    scheduled_at=row["scheduled_at"],
                    risk_level=row["risk_level"],
                )
                for row in upcoming_risk
            ],
            total_idle_minutes=idle_minutes,
            estimated_revenue_lost_to_idle_capacity=estimated_revenue_lost_to_idle_capacity,
            professionals_without_availability_count=professionals_without_availability_count,
        )

    async def get_plan_loss_ranking(self, date_from: date, date_to: date) -> PlanLossRankingResponse:
        """Une as três fontes de perda por convênio que já existem
        separadas no sistema (buraco de cobrança, divergência de
        recebimento, valor em risco de glosa) num único ranking por
        operadora — mesmos números de ExecutiveSummaryResponse, só
        quebrados por convênio em vez de somados no tenant inteiro."""
        hole_by_plan = await self.analytics_repo.financial_hole_by_plan(date_from, date_to)
        gap_by_plan = await self.analytics_repo.payment_gap_by_plan(date_from, date_to)
        denial_by_plan = await self.analytics_repo.denial_risk_value_by_plan(date_from, date_to)

        plan_names = set(hole_by_plan) | set(gap_by_plan) | set(denial_by_plan)
        items = [
            PlanLossItem(
                plan_name=plan_name,
                financial_hole=hole_by_plan.get(plan_name, 0.0),
                payment_gap=gap_by_plan.get(plan_name, 0.0),
                denial_risk_value=denial_by_plan.get(plan_name, 0.0),
                total_loss=(
                    hole_by_plan.get(plan_name, 0.0) + gap_by_plan.get(plan_name, 0.0) + denial_by_plan.get(plan_name, 0.0)
                ),
            )
            for plan_name in plan_names
        ]
        items.sort(key=lambda item: item.total_loss, reverse=True)

        return PlanLossRankingResponse(period_start=date_from, period_end=date_to, plans=items)

    async def get_contract_utilization(self, date_from: date, date_to: date) -> ContractUtilizationResponse:
        """Ver DECISÃO completa em AnalyticsRepository.contract_utilization
        sobre a semântica de idle_catalog_value. Utilization_pct é
        calculado aqui (não em SQL) por ser uma divisão simples sobre
        dado já agregado — mesmo raciocínio de manter o SQL cru restrito
        ao que só o banco faz bem (agregação em volume), com a aritmética
        final em Python."""
        rows = await self.analytics_repo.contract_utilization(date_from, date_to)
        contracts = [
            ContractUtilizationItem(
                contract_id=row["contract_id"],
                plan_name=row["plan_name"],
                valid_from=row["valid_from"],
                valid_until=row["valid_until"],
                total_items=row["total_items"],
                items_billed=row["items_billed"],
                utilization_pct=(row["items_billed"] / row["total_items"] * 100) if row["total_items"] > 0 else 0.0,
                idle_catalog_value=row["idle_catalog_value"],
            )
            for row in rows
        ]
        return ContractUtilizationResponse(period_start=date_from, period_end=date_to, contracts=contracts)

    async def get_denial_risk_distribution(self, date_from: date, date_to: date) -> DenialRiskDistributionResponse:
        """
        Achado do Parecer Técnico "Boletim Insighta" (revisão 2): esta
        resposta e o insight "% do faturado em risco de glosa" cobriam o
        MESMO corte de dado (nível de risco no período) em duas
        granularidades diferentes — contagem aqui, valor agregado lá.
        Busca as duas agregações (já existiam separadas, ver
        AnalyticsRepository.denial_risk_count_breakdown/
        denial_risk_value_breakdown) e devolve os dois lado a lado por
        nível, pro frontend alternar a visão sem precisar de duas telas.
        """
        count_breakdown = await self.analytics_repo.denial_risk_count_breakdown(date_from, date_to)
        value_breakdown = await self.analytics_repo.denial_risk_value_breakdown(date_from, date_to)
        levels = set(count_breakdown) | set(value_breakdown)
        items = [
            DenialRiskDistributionItem(
                level=level, count=count_breakdown.get(level, 0), value=value_breakdown.get(level, 0.0)
            )
            for level in levels
        ]
        return DenialRiskDistributionResponse(
            period_start=date_from,
            period_end=date_to,
            items=items,
            total_reviewed=sum(count_breakdown.values()),
            total_value_reviewed=sum(value_breakdown.values()),
        )

    async def _period_insights_input(
        self,
        date_from: date,
        date_to: date,
        *,
        appeals_due_soon: int = 0,
        annual_goal_context: "_AnnualGoalContext | None" = None,
        upcoming_risk_count_by_weekday: dict[int, int] | None = None,
        professional_utilization_rates: list[tuple[str, str, float]] | None = None,
        include_agenda_text_breakdowns: bool = True,
        stale_open_lotes: tuple[int, int | None] = (0, None),
        expiring_contracts: tuple[int, str | None, int | None] = (0, None, None),
        payment_gap_without_appeal: tuple[int, float] = (0, 0.0),
        yoy_last_year_appointment_count: int | None = None,
        early_churn_risk_count: int = 0,
    ) -> InsightsPeriodInput:
        # Achado 8 da Auditoria de Templates e Insights (baixo) —
        # `booking_channel_no_show_counts`/`cancellation_reason_counts`
        # só são lidos de `current` por
        # _booking_channel_no_show_insight/_cancellation_reason_insight
        # (nunca de `previous`, mesmo raciocínio de appeals_due_soon
        # acima). `include_agenda_text_breakdowns=False` pula as 2
        # consultas quando este helper é chamado para o período ANTERIOR
        # (ver get_smart_insights) — antes rodavam nas duas chamadas,
        # descartando o resultado da segunda.
        # appeals_due_soon é passado de fora, não recalculado aqui: é um
        # estado "AGORA" (prazo vencendo hoje), não algo que faça sentido
        # perguntar de novo para o "período anterior" — comparar contra
        # si mesmo sempre daria delta zero. Só o período ATUAL recebe o
        # valor real; o anterior fica no default 0 do dataclass. Mesmo
        # raciocínio para stale_open_lotes (lotes abertos há muito tempo,
        # ver LoteRepository.stale_open_lotes_summary/
        # _stale_open_lotes_insight) — "O que resta em aberto" da
        # Auditoria de Templates e Insights, próxima peça do mesmo padrão
        # que Guia/coparticipação já fecharam.
        billing = await self.reporting_repo.billing_summary(date_from, date_to)
        financial_hole = await self.analytics_repo.financial_hole_total(date_from, date_to)
        payment_gap = await self.analytics_repo.payment_gap_total(date_from, date_to)
        # Concentração de receita por convênio (Raio-X da Receita, frente
        # "Gestão eficiente") — só `current` é lido por
        # _revenue_concentration_insight, mas buscado nos dois períodos
        # pelo mesmo motivo de professional_denial_rates abaixo (o
        # helper monta o input inteiro pra qualquer um dos dois períodos,
        # sem saber de fora qual vai ser usado por qual insight).
        revenue_by_plan = await self.analytics_repo.revenue_by_plan(date_from, date_to)
        # PMR (achado da auditoria "Veredito do Gestor Clínico") —
        # chamado uma vez por período (atual e anterior), mesmo padrão de
        # financial_hole/payment_gap acima: _payment_lag_insight compara
        # os dois, ao contrário de appeals_due_soon/stale_open_lotes
        # (estado "AGORA", só o período atual importa).
        payment_lag_days, payment_lag_settled_count = await self.analytics_repo.payment_lag_total(date_from, date_to)
        # ROI de marketing (Raio-X da Receita, frente "Melhorias") —
        # mesmas duas chamadas que ReportDataService já fazia pro
        # relatório semanal (ver ReportingRepository.marketing_spend_total/
        # revenue_from_campaign_patients), agora também alimentando o
        # feed da Sala de Comando. Período-escopado como financial_hole/
        # payment_gap acima (_marketing_roi_insight compara os dois).
        marketing_spend_total = await self.reporting_repo.marketing_spend_total(date_from, date_to)
        marketing_revenue_attributed = await self.reporting_repo.revenue_from_campaign_patients(date_from, date_to)
        avg_utilization = await self._avg_utilization(date_from, date_to)
        denial_findings = await self.analytics_repo.denial_findings_by_plan(date_from, date_to)
        # "Sempre a partir de agora", nunca da janela do dashboard — ver
        # DECISÃO em AnalyticsRepository.no_show_risk_breakdown. Isso é
        # chamado uma vez para o período ATUAL e outra para o ANTERIOR
        # (ver _period_insights_input), mas como não depende mais da
        # janela, as duas chamadas devolvem o mesmo resultado — sem
        # problema: high_risk_no_show_count do período ANTERIOR nunca é
        # lido por nenhum insight (ver _no_show_risk_insight, só olha
        # `current`).
        risk_breakdown = await self.analytics_repo.no_show_risk_breakdown(as_of=datetime.now(timezone.utc))
        weekday_histogram = await self.analytics_repo.appointment_weekday_histogram(date_from, date_to)
        weekday_no_show_counts = await self.analytics_repo.weekday_no_show_rate_breakdown(date_from, date_to)
        # Achado do Dicionário de Dados: campos novos do Template de
        # Agenda (booking_channel/cancellation_reason) — ver DECISÃO em
        # smart_insights_engine.py::_booking_channel_no_show_insight /
        # _cancellation_reason_insight. Só buscados quando o resultado de
        # fato vai ser usado (ver Achado 8 acima).
        booking_channel_no_show_counts: dict[str, tuple[int, int]] = {}
        cancellation_reason_counts: dict[str, int] = {}
        total_cancelled_count = 0
        if include_agenda_text_breakdowns:
            raw_channel_counts = await self.analytics_repo.booking_channel_no_show_rate_breakdown(date_from, date_to)
            # Achado 3 da Auditoria (alto) — reagrupa grafias equivalentes
            # de canal ("WhatsApp"/"whatsapp") antes de expor ao motor de
            # insights, ver DECISÃO em _regroup_text_no_show_counts.
            booking_channel_no_show_counts = _regroup_text_no_show_counts(raw_channel_counts)
            raw_reason_counts, total_cancelled_count = await self.analytics_repo.cancellation_reason_breakdown(
                date_from, date_to
            )
            # Mesma DECISÃO de canal, agora para motivo de cancelamento.
            cancellation_reason_counts = _regroup_text_counts(raw_reason_counts)
        # Achado do Dicionário de Dados: campos novos do Template de
        # Faturamento (item_type/coparticipation_value) — ver DECISÃO em
        # smart_insights_engine.py::_opme_concentration_insight /
        # _coparticipation_visibility_insight. Diferente dos 2 acima,
        # estes SÃO lidos também do período anterior (Achado 4: os dois
        # insights agora comparam contra o período anterior para evitar
        # alerta permanente) — sempre buscados, nunca pulados.
        item_type_charged_value = await self.analytics_repo.item_type_charged_value_breakdown(date_from, date_to)
        coparticipation_total, coparticipation_billing_count, total_billing_count = (
            await self.analytics_repo.coparticipation_summary(date_from, date_to)
        )
        # Épico F4.2 — "estado AGORA", mesmo raciocínio de risk_breakdown
        # acima: só o período ATUAL é lido por
        # _coparticipation_unconfirmed_insight, mas buscar pros dois é
        # inofensivo (mesma query barata, sem quebrar o padrão de
        # sempre buscar tudo aqui).
        coparticipation_unconfirmed_value, coparticipation_unconfirmed_count = (
            await self.analytics_repo.coparticipation_unconfirmed_summary(date_from, date_to)
        )
        # Épico F2.3 — mesmo raciocínio de coparticipation_unconfirmed_*
        # acima: "estado AGORA", só o período atual é lido por
        # _opme_documentation_unconfirmed_insight.
        opme_documentation_unconfirmed_value, opme_documentation_unconfirmed_count = (
            await self.analytics_repo.opme_documentation_unconfirmed_summary(date_from, date_to)
        )
        risk_value_breakdown = await self.analytics_repo.denial_risk_value_breakdown(date_from, date_to)
        denial_risk_pct, denial_at_risk_value = _denial_risk_pct(risk_value_breakdown)
        professional_denial_rates = await self.analytics_repo.professional_denial_rates(date_from, date_to)

        reason_counts: dict[tuple[str, str, str], int] = {}
        for plan_id, plan_name, reasons in denial_findings:
            for reason_code in reasons:
                key = (plan_id, plan_name, reason_code)
                reason_counts[key] = reason_counts.get(key, 0) + 1

        return InsightsPeriodInput(
            denial_reason_counts=[
                DenialReasonCount(plan_id=plan_id, plan_name=plan_name, reason_code=reason_code, count=count)
                for (plan_id, plan_name, reason_code), count in reason_counts.items()
            ],
            financial_hole_total=financial_hole,
            payment_gap_total=payment_gap,
            total_value_saved=billing["total_value_saved"],
            avg_capacity_utilization=avg_utilization,
            high_risk_no_show_count=risk_breakdown.get("alto", 0),
            appeals_due_soon_count=appeals_due_soon,
            weekday_appointment_counts=weekday_histogram,
            weekday_no_show_counts=weekday_no_show_counts,
            denial_risk_pct=denial_risk_pct,
            denial_at_risk_value=denial_at_risk_value,
            annual_revenue_goal=annual_goal_context.annual_revenue_goal if annual_goal_context else None,
            elapsed_year_fraction=annual_goal_context.elapsed_year_fraction if annual_goal_context else None,
            ytd_billed_total=annual_goal_context.ytd_billed_total if annual_goal_context else 0.0,
            inactive_patients_count=annual_goal_context.inactive_patients_count if annual_goal_context else 0,
            professional_denial_rates=professional_denial_rates,
            upcoming_risk_count_by_weekday=upcoming_risk_count_by_weekday or {},
            professional_utilization_rates=professional_utilization_rates or [],
            booking_channel_no_show_counts=booking_channel_no_show_counts,
            cancellation_reason_counts=cancellation_reason_counts,
            total_cancelled_count=total_cancelled_count,
            total_billed=billing["total_billed"],
            item_type_charged_value=item_type_charged_value,
            coparticipation_total=coparticipation_total,
            coparticipation_billing_count=coparticipation_billing_count,
            total_billing_count=total_billing_count,
            coparticipation_unconfirmed_value=coparticipation_unconfirmed_value,
            coparticipation_unconfirmed_count=coparticipation_unconfirmed_count,
            opme_documentation_unconfirmed_value=opme_documentation_unconfirmed_value,
            opme_documentation_unconfirmed_count=opme_documentation_unconfirmed_count,
            stale_open_lotes_count=stale_open_lotes[0],
            oldest_open_lote_age_days=stale_open_lotes[1],
            avg_days_to_receive=payment_lag_days,
            payment_lag_settled_count=payment_lag_settled_count,
            expiring_contracts_count=expiring_contracts[0],
            soonest_expiring_contract_plan_name=expiring_contracts[1],
            soonest_expiring_contract_days=expiring_contracts[2],
            revenue_by_plan=revenue_by_plan,
            marketing_spend_total=marketing_spend_total,
            marketing_revenue_attributed=marketing_revenue_attributed,
            payment_gap_without_appeal_count=payment_gap_without_appeal[0],
            payment_gap_without_appeal_value=payment_gap_without_appeal[1],
            yoy_last_year_appointment_count=yoy_last_year_appointment_count,
            early_churn_risk_count=early_churn_risk_count,
        )

    async def get_smart_insights(
        self,
        date_from: date,
        date_to: date,
        *,
        tenant_id: str,
        network_benchmark: list[tuple[str, str, float, float]] | None = None,
    ) -> SmartInsightsResponse:
        previous = _previous_period(date_from, date_to)
        appeals_due_soon = await self.appeal_repo.count_due_within(
            as_of=date.today(), horizon_days=APPEAL_DEADLINE_ALERT_HORIZON_DAYS
        )
        # "O que resta em aberto" da Auditoria de Templates e Insights:
        # estado "AGORA" (mesmo raciocínio de appeals_due_soon acima) —
        # ver LoteRepository.stale_open_lotes_summary/_STALE_LOTE_AFTER_DAYS.
        stale_open_lotes = await self.lote_repo.stale_open_lotes_summary(
            as_of=datetime.now(timezone.utc), stale_after_days=_STALE_LOTE_AFTER_DAYS
        )
        # Estado "AGORA" (mesmo raciocínio de appeals_due_soon/
        # stale_open_lotes acima) — usado tanto pelo alerta de contrato
        # vencendo quanto pela meta anual logo abaixo.
        today = date.today()

        # Raio-X da Receita, frente "Evitando perdas": ver
        # ContractRepository.expiring_without_renewal_summary. Só o
        # primeiro (o que vence primeiro) alimenta o insight, mesmo
        # critério de "só o pior caso" do resto do motor.
        expiring_contracts_rows = await self.contract_repo.expiring_without_renewal_summary(
            today, CONTRACT_EXPIRING_ALERT_HORIZON_DAYS
        )
        expiring_contracts = (
            len(expiring_contracts_rows),
            expiring_contracts_rows[0]["plan_name"] if expiring_contracts_rows else None,
            (expiring_contracts_rows[0]["valid_until"] - today).days if expiring_contracts_rows else None,
        )
        # Raio-X da Receita, frente "Evitando perdas": backlog ATUAL,
        # mesmo raciocínio de appeals_due_soon acima — ver
        # AnalyticsRepository.payment_gap_without_appeal_summary.
        payment_gap_without_appeal = await self.analytics_repo.payment_gap_without_appeal_summary()

        # Raio-X da Receita, frente "Prevendo movimentos": mesmo período,
        # um ano antes — ver _year_ago_period e
        # smart_insights_engine.py::_yoy_seasonality_insight. Reaproveita
        # appointment_weekday_histogram (mesma query de weekday_histogram
        # do período atual/anterior), só somando os 7 baldes — o insight
        # só precisa do TOTAL, não da distribuição por dia.
        year_ago = _year_ago_period(date_from, date_to)
        year_ago_histogram = await self.analytics_repo.appointment_weekday_histogram(year_ago.start, year_ago.end)
        yoy_last_year_appointment_count = sum(year_ago_histogram.values())

        # Raio-X da Receita, frente "Prevendo movimentos": estado "AGORA",
        # mesmo raciocínio de appeals_due_soon acima — ver
        # AnalyticsRepository.count_early_churn_risk_patients.
        early_churn_risk_count = await self.analytics_repo.count_early_churn_risk_patients(
            today, min_visits=_EARLY_CHURN_MIN_VISITS, gap_multiplier=_EARLY_CHURN_GAP_MULTIPLIER,
            inactive_after_days=_INACTIVE_PATIENT_AFTER_DAYS,
        )

        # Meta anual (Auditoria Go-Live, terceiro exemplo do briefing de
        # redesenho) — só calculado para o período ATUAL, nunca para o
        # anterior (não existe "meta do período anterior", ver
        # _AnnualGoalContext). tenant.annual_revenue_goal é lido direto do
        # Tenant (tabela sem RLS — mesmo motivo de TenantRepository já
        # existir separado, ver seu docstring), nunca calculado sozinho.
        tenant = await self.tenant_repo.get_by_id(uuid.UUID(tenant_id))
        annual_goal_context = _AnnualGoalContext(
            annual_revenue_goal=float(tenant.annual_revenue_goal) if tenant and tenant.annual_revenue_goal else None,
            elapsed_year_fraction=_elapsed_year_fraction(today),
            ytd_billed_total=await self.analytics_repo.ytd_billed_total(today),
            inactive_patients_count=await self.analytics_repo.inactive_patients_count(
                today, inactive_after_days=_INACTIVE_PATIENT_AFTER_DAYS
            ),
        )

        # Ocupação por profissional no período ATUAL — mesmo cálculo de
        # get_agenda_metrics (capacity_service.get_utilization), só que
        # aqui alimenta o texto do insight de queda de agenda (ver DECISÃO
        # em smart_insights_engine.py::_capacity_drop_insight nomear QUEM
        # está ocioso). Só quem tem grade cadastrada entra (available_minutes
        # > 0) — sem grade, "ocupação" não tem denominador (mesmo motivo de
        # professionals_without_availability_count). Estado "AGORA" do
        # período atual, mesmo raciocínio de professional_denial_rates —
        # não faz sentido perguntar "ocupação do período anterior" pra
        # decidir quem nomear hoje.
        professionals = await self.professional_repo.list_active()
        professional_utilization_rates: list[tuple[str, str, float]] = []
        for professional in professionals:
            result = await self.capacity_service.get_utilization(professional.id, date_from, date_to)
            if result.available_minutes > 0:
                professional_utilization_rates.append((str(professional.id), professional.full_name, result.utilization_rate))

        # Agendamentos futuros com risco médio/alto, por dia da semana —
        # alimenta o "e olha, você já tem N marcadas com risco pra esse
        # dia" de _weekday_no_show_rate_insight.
        upcoming_risk_count_by_weekday = await self.analytics_repo.upcoming_risk_count_by_weekday(
            as_of=datetime.now(timezone.utc)
        )

        current_input = await self._period_insights_input(
            date_from,
            date_to,
            appeals_due_soon=appeals_due_soon,
            annual_goal_context=annual_goal_context,
            upcoming_risk_count_by_weekday=upcoming_risk_count_by_weekday,
            professional_utilization_rates=professional_utilization_rates,
            stale_open_lotes=stale_open_lotes,
            expiring_contracts=expiring_contracts,
            payment_gap_without_appeal=payment_gap_without_appeal,
            yoy_last_year_appointment_count=yoy_last_year_appointment_count,
            early_churn_risk_count=early_churn_risk_count,
        )
        previous_input = await self._period_insights_input(
            previous.start, previous.end, include_agenda_text_breakdowns=False
        )
        avg_charged = await self.analytics_repo.avg_charged_value(date_from, date_to)
        estimated_revenue_at_risk = current_input.high_risk_no_show_count * avg_charged

        idle_minutes, idle_booked_minutes, idle_total_appointments = await self._idle_capacity_totals(date_from, date_to)
        estimated_idle_capacity_revenue_lost = estimate_idle_capacity_revenue_lost(
            idle_minutes=idle_minutes,
            booked_minutes=idle_booked_minutes,
            total_appointments=idle_total_appointments,
            avg_charged_value=avg_charged,
        )

        # Comparativo entre clínicas como manchete do feed (Sala de Comando
        # 2.0, Nível 1) — `network_benchmark` vem de fora (endpoint) porque
        # é a única fonte de dado deste método que exige sessão SEM tenant
        # (ver DECISÃO em app/api/v1/endpoints/analytics.py::get_smart_insights
        # e app/sql/032_network_benchmark.sql). Cada item já vem com
        # cohort suficiente (o SQL nunca devolve mediana sem amostra
        # mínima) — só o pior desvio (maior impacto projetado) vira
        # manchete, mesmo critério do Radar de Profissional. `key` (além
        # do `label`) identifica qual métrica é "denial" — só essa recebe
        # o "por onde começar" (motivo de glosa mais comum da própria
        # clínica, ver DECISÃO em build_network_comparativo_insight); a
        # métrica de falta recebe o equivalente pra taxa de falta (pior
        # dia da semana da própria clínica, ver describe_worst_no_show_weekday) —
        # mesmo "por onde começar", fonte de dado diferente.
        extra_insights = []
        if network_benchmark:
            total_billed = (await self.reporting_repo.billing_summary(date_from, date_to))["total_billed"]
            top_reason_label = None
            # Filtra fora "value_below_contract_revenue_leak" antes de somar
            # (ver DECISÃO em smart_insights_engine.is_true_denial_risk_reason)
            # — achado do usuário: sem isso, o "por onde começar" da glosa
            # podia apontar "o motivo mais comum de recusa tem sido cobrar
            # mais barato", que não é recusa nenhuma, é vazamento de receita.
            denial_reasons_for_hint = [
                c for c in current_input.denial_reason_counts if is_true_denial_risk_reason(c.reason_code)
            ]
            if denial_reasons_for_hint:
                reason_totals: dict[str, int] = {}
                for reason_count in denial_reasons_for_hint:
                    reason_totals[reason_count.reason_code] = reason_totals.get(reason_count.reason_code, 0) + reason_count.count
                top_reason_code = max(reason_totals, key=lambda code: reason_totals[code])
                top_reason_label = describe_denial_reason(top_reason_code)
            top_weekday_label = describe_worst_no_show_weekday(current_input)

            candidates = [
                insight
                for (key, label, your_rate, network_median) in network_benchmark
                if (
                    insight := build_network_comparativo_insight(
                        metric_label=label,
                        category="faturamento" if key == "denial" else "agenda",
                        your_rate=your_rate,
                        network_median=network_median,
                        total_billed=total_billed,
                        top_reason_label=top_reason_label if key == "denial" else None,
                        top_weekday_label=top_weekday_label if key == "no_show" else None,
                    )
                )
                is not None
            ]
            if candidates:
                extra_insights.append(max(candidates, key=lambda i: (i.financial_impact or 0)))

        # Épico F2.1 do Plano Diretor ("Calibração por especialidade/porte")
        # — `tenant` já foi buscado acima (annual_revenue_goal); reaproveita
        # o mesmo objeto para resolver o limiar de risco de glosa
        # configurado desta clínica, sem consulta extra.
        denial_risk_warning_threshold, denial_risk_critical_threshold = resolve_denial_risk_thresholds(tenant)
        insights = generate_insights(
            current_input,
            previous_input,
            estimated_revenue_at_risk,
            estimated_idle_capacity_revenue_lost,
            extra_insights=extra_insights,
            denial_risk_warning_threshold=denial_risk_warning_threshold,
            denial_risk_critical_threshold=denial_risk_critical_threshold,
        )

        return SmartInsightsResponse(
            period_start=date_from,
            period_end=date_to,
            insights=[
                SmartInsightResponse(
                    severity=i.severity,
                    category=i.category,
                    title=i.title,
                    message=i.message,
                    financial_impact=i.financial_impact,
                    is_new=i.is_new,
                    action_label=i.action_label,
                    action_href=i.action_href,
                )
                for i in insights
            ],
        )

    # _PRIORITY_QUEUE_DEFAULT_LIMIT: quantos itens a tela "Hoje" mostra por
    # padrão — 10 é "cabe numa tela sem rolar muito" para o gestor de 5
    # minutos que a F1.1 do Plano Diretor descreve; `total_considered` no
    # response deixa claro quando há mais itens fora do corte.
    _PRIORITY_QUEUE_DEFAULT_LIMIT = 10

    async def get_priority_queue(
        self,
        date_from: date,
        date_to: date,
        *,
        tenant_id: str,
        network_benchmark: list[tuple[str, str, float, float]] | None = None,
        limit: int = _PRIORITY_QUEUE_DEFAULT_LIMIT,
    ) -> PriorityQueueResponse:
        """
        Épico F1.1 do Plano Diretor ("Fila única de ação priorizada"):
        "Hoje os 39 mecanismos vivem espalhados em abas [...] O gestor
        decide sozinho, de cabeça, o que atacar primeiro." Esta fila
        reaproveita 100% do cálculo já pronto — get_smart_insights (que
        já cobre a maior parte do motor, incluindo o Comparativo) MAIS
        os 3 painéis do Raio-X da Receita que concentram perda real mas
        NUNCA viram card de feed sozinhos (ver DECISÃO no boletim
        técnico: "são painéis, não cards de feed"): ranking de perda por
        convênio, utilização de contrato ociosa, e o profissional com
        menor receita por hora ocupada. Nenhum motor novo, só uma
        camada de agregação e ranqueamento por cima de serviços que já
        existem — exatamente o que o tech-note do épico pede.

        Cada painel entra só quando tem ALGO material a mostrar (nunca
        um card vazio "0 de perda") e só o PIOR item de cada um — mesmo
        critério de "só o pior caso vira manchete" já usado em
        _professional_outlier_insight/build_network_comparativo_insight.
        """
        insights_response = await self.get_smart_insights(
            date_from, date_to, tenant_id=tenant_id, network_benchmark=network_benchmark
        )
        items: list[PriorityQueueItem] = [
            PriorityQueueItem(
                severity=i.severity,
                category=i.category,
                title=i.title,
                message=i.message,
                financial_impact=i.financial_impact,
                is_new=i.is_new,
                action_label=i.action_label,
                action_href=i.action_href,
                source="insight",
            )
            for i in insights_response.insights
        ]

        loss_ranking = await self.get_plan_loss_ranking(date_from, date_to)
        if loss_ranking.plans and loss_ranking.plans[0].total_loss > 0:
            worst_plan = loss_ranking.plans[0]
            items.append(
                PriorityQueueItem(
                    severity="critical",
                    category="faturamento",
                    title=f"Maior perda concentrada no convênio {worst_plan.plan_name}",
                    message=(
                        f"Somando buraco de cobrança, pagamento a menor e valor em risco de glosa, "
                        f"{worst_plan.plan_name} concentra R$ {worst_plan.total_loss:,.2f} de perda no período — "
                        "o maior entre todos os convênios faturados. Ver o ranking completo pra decidir com qual "
                        "convênio conversar primeiro."
                    ).replace(",", "X").replace(".", ",").replace("X", "."),
                    financial_impact=worst_plan.total_loss,
                    action_label="Ver ranking de perda por convênio",
                    action_href="/",
                    source="raiox",
                )
            )

        utilization = await self.get_contract_utilization(date_from, date_to)
        if utilization.contracts and utilization.contracts[0].idle_catalog_value > 0:
            worst_contract = utilization.contracts[0]
            items.append(
                PriorityQueueItem(
                    severity="warning",
                    category="faturamento",
                    title=f"Contrato de {worst_contract.plan_name} com catálogo pouco utilizado",
                    message=(
                        f"Só {worst_contract.utilization_pct:.0f}% dos procedimentos negociados com "
                        f"{worst_contract.plan_name} foram faturados no período — R$ {worst_contract.idle_catalog_value:,.2f} "
                        "em valor de tabela contratado nunca cobrado. Pode ser linha de serviço parada, não "
                        "necessariamente um problema, mas vale investigar por quê."
                    ).replace(",", "X").replace(".", ",").replace("X", "."),
                    financial_impact=worst_contract.idle_catalog_value,
                    action_label="Ver utilização de contrato",
                    action_href="/",
                    source="raiox",
                )
            )

        profitability = await self.get_profitability(date_from, date_to)
        rated_professionals = [p for p in profitability.by_professional if p.revenue_per_hour is not None]
        # Exige pelo menos 2 pra "pior" ter sentido comparativo — com 1
        # profissional só, não existe "pior que quem" (mesmo princípio
        # de amostra mínima do resto do motor: nunca inventa confiança
        # sem ter contra o que comparar).
        if len(rated_professionals) >= 2:
            worst_professional = min(rated_professionals, key=lambda p: p.revenue_per_hour or 0)
            best_rate = max(p.revenue_per_hour or 0 for p in rated_professionals)
            if best_rate > 0 and (worst_professional.revenue_per_hour or 0) < best_rate:
                items.append(
                    PriorityQueueItem(
                        severity="warning",
                        category="estrategia",
                        title=f"{worst_professional.full_name} com a menor receita por hora ocupada da equipe",
                        message=(
                            f"R$ {(worst_professional.revenue_per_hour or 0):,.2f}/hora ocupada, contra até "
                            f"R$ {best_rate:,.2f}/hora de outro profissional da equipe no mesmo período. Pode ser mix "
                            "de procedimento, tabela de convênio, ou algo a conversar — sem inventar o motivo aqui."
                        ).replace(",", "X").replace(".", ",").replace("X", "."),
                        financial_impact=None,
                        action_label="Ver rentabilidade por profissional",
                        # "#tab:id" — mesma convenção de smart_insights_engine.Insight:
                        # a fila renderiza dentro da própria Sala de Comando, então
                        # troca de aba (aba "Rentabilidade" já existe), não navega
                        # pra fora (ver InsightActionButton no frontend).
                        action_href="#tab:rentabilidade",
                        source="raiox",
                    )
                )

        severity_rank = {"critical": 0, "comparativo": 1, "warning": 2, "positive": 3}
        items.sort(key=lambda i: (i.financial_impact is None, -(i.financial_impact or 0), severity_rank.get(i.severity, 99)))

        return PriorityQueueResponse(
            period_start=date_from,
            period_end=date_to,
            items=items[:limit],
            total_considered=len(items),
        )

    async def get_health_score(self, tenant_id: str) -> HealthScoreResponse:
        """
        Nota de Saúde Financeira — ver DECISÃO completa em
        health_score_engine.py (regras determinísticas, componente sem
        amostra é excluído, nunca vira zero). Janela sempre fixa (ver
        _HEALTH_SCORE_WINDOW_DAYS acima), nunca a do seletor de período.

        `tenant_id` (Épico F2.1 do Plano Diretor — "Calibração por
        especialidade/porte"): busca o tenant só para resolver os tetos
        configurados (ver resolve_health_score_ceilings) — mesmo padrão de
        get_smart_insights reaproveitando `self.tenant_repo`.

        "Junta Técnica Insighta": o teto de falta (`no_show_rate_ceiling`)
        agora também considera a MISTURA de especialidades do período —
        ver DECISÃO completa em
        health_score_engine.resolve_no_show_ceiling_for_period.
        """
        today = date.today()
        window_start_date = today - timedelta(days=_HEALTH_SCORE_WINDOW_DAYS)
        window_start_dt = datetime.combine(window_start_date, datetime.min.time(), tzinfo=timezone.utc)

        tenant = await self.tenant_repo.get_by_id(uuid.UUID(tenant_id))
        denial_rate_ceiling, _default_no_show_rate_ceiling = resolve_health_score_ceilings(tenant)

        risk_value_breakdown = await self.analytics_repo.denial_risk_value_breakdown(window_start_date, today)
        denial_risk_pct_0_100, _denial_at_risk_value = _denial_risk_pct(risk_value_breakdown)
        # _denial_risk_pct devolve 0-100 (mesma escala 0-100 usada em
        # "faturamentos travados por risco" na tira de KPIs da Sala de
        # Comando) — health_score_engine.py trabalha com fração 0.0-1.0
        # em TODOS os componentes (mesma escala de no_show_count/
        # no_show_total), por isso a conversão aqui.
        denial_risk_pct = denial_risk_pct_0_100 / 100 if denial_risk_pct_0_100 is not None else None
        no_show_count, no_show_total = await self.analytics_repo.overall_no_show_rate(window_start_date, today)
        appeal_counts = await self.appeal_repo.count_resolved_by_status(since=window_start_dt)

        no_show_rate_ceiling = _default_no_show_rate_ceiling
        if no_show_total > 0 and (tenant is None or tenant.health_score_no_show_ceiling is None):
            # Só busca a quebra por especialidade quando de fato existe um
            # componente de falta pra calcular E o gestor não configurou um
            # teto manual (que sempre vence, ver resolve_no_show_ceiling_for_period)
            # — evita 2 queries extras num tenant novo sem atendimento
            # resolvido ainda, ou que já fez a própria calibração.
            counts_by_specialty = await self.analytics_repo.no_show_rate_by_specialty(window_start_date, today)
            monthly_rates_by_specialty = await monthly_no_show_rates_by_specialty(self.analytics_repo)
            no_show_rate_ceiling = resolve_no_show_ceiling_for_period(
                tenant, counts_by_specialty=counts_by_specialty, monthly_rates_by_specialty=monthly_rates_by_specialty
            )

        result = compute_health_score(
            denial_risk_pct=denial_risk_pct,
            no_show_count=no_show_count,
            no_show_total=no_show_total,
            appeal_deferred_count=appeal_counts["deferido"],
            appeal_indeferido_count=appeal_counts["indeferido"],
            denial_rate_ceiling=denial_rate_ceiling,
            no_show_rate_ceiling=no_show_rate_ceiling,
        )

        # Tendência (ver DECISÃO em app/sql/034_health_score_snapshots.sql):
        # só existe quando (a) a nota atual pôde ser calculada e (b) já
        # existe uma fotografia gravada de referência com nota não-nula
        # — base nova, sem 3 meses de histórico ainda, não tem tendência
        # nenhuma para mostrar (nunca inventa um "0%" ou repete a nota
        # atual como se fosse a de 3 meses atrás).
        trend = None
        if result.score is not None:
            reference_date = today - timedelta(days=_HEALTH_SCORE_TREND_REFERENCE_DAYS)
            reference = await self.health_score_snapshot_repo.get_reference_snapshot(on_or_before=reference_date)
            if reference is not None and reference.score is not None:
                trend = HealthScoreTrendResponse(
                    reference_score=reference.score,
                    reference_month=reference.snapshot_month,
                    delta=result.score - reference.score,
                )

        return HealthScoreResponse(
            score=result.score,
            components=[
                HealthScoreComponentResponse(key=c.key, label=c.label, rate=c.rate, sub_score=c.sub_score, weight=c.weight)
                for c in result.components
            ],
            window_days=_HEALTH_SCORE_WINDOW_DAYS,
            trend=trend,
        )

    async def get_satisfaction_summary(self) -> SatisfactionSummaryResponse:
        """
        "Equilíbrio Insighta" (Balanced Scorecard, perna Cliente,
        mecanismo 2) — resumo de NPS/satisfação pós-atendimento. Janela
        fixa (mesmo padrão de get_health_score, ver DECISÃO em
        _SATISFACTION_WINDOW_DAYS acima) — não segue o seletor de período
        da tela.

        Tendência contra a janela ANTERIOR de mesma duração (mesmo
        formato PeriodKPI usado no resto da Sala de Comando, ver
        ExecutiveSummaryResponse) — não uma fotografia gravada tipo
        Health Score, porque aqui a amostra já é naturalmente pequena
        (nem todo atendimento é avaliado); comparar contra o período
        imediatamente anterior é mais honesto do que esperar meses de
        histórico acumulado só para ter UMA comparação.
        """
        today = date.today()
        window_start = today - timedelta(days=_SATISFACTION_WINDOW_DAYS)
        previous_window_end = window_start - timedelta(days=1)
        previous_window_start = previous_window_end - timedelta(days=_SATISFACTION_WINDOW_DAYS)

        current = await self.analytics_repo.satisfaction_score_breakdown(window_start, today)
        current_count = sum(current.values())

        average_score = None
        if current_count > 0:
            current_avg = sum(score * count for score, count in current.items()) / current_count
            previous = await self.analytics_repo.satisfaction_score_breakdown(previous_window_start, previous_window_end)
            previous_count = sum(previous.values())
            if previous_count > 0:
                previous_avg = sum(score * count for score, count in previous.items()) / previous_count
                delta_pct = ((current_avg - previous_avg) / previous_avg) * 100 if previous_avg != 0 else None
            else:
                # Sem janela anterior pra comparar — nunca inventa "0% de
                # variação" (mesmo raciocínio de "amostra ausente != sem
                # mudança" do resto do produto). previous_value só existe
                # aqui porque o schema exige um float; delta_pct=None é o
                # sinal real de "sem comparação", o único que o frontend lê.
                previous_avg = current_avg
                delta_pct = None

            average_score = PeriodKPI(value=round(current_avg, 2), previous_value=round(previous_avg, 2), delta_pct=delta_pct)

        return SatisfactionSummaryResponse(
            average_score=average_score,
            response_count=current_count,
            distribution={i: current.get(i, 0) for i in range(1, 6)},
            window_days=_SATISFACTION_WINDOW_DAYS,
        )

    async def get_inactive_patients(self) -> InactivePatientsResponse:
        """
        Carteira de pacientes inativos — a lista real por trás da
        recomendação "reativar quem não voltou" do insight de meta
        anual (ver DECISÃO em smart_insights_engine.py::_annual_goal_insight
        e AnalyticsRepository.list_inactive_patients). Mesmo piso de dias
        (`_INACTIVE_PATIENT_AFTER_DAYS`) usado nos dois lugares — a
        contagem que o insight cita e a lista aqui precisam bater.
        """
        today = date.today()
        total_count = await self.analytics_repo.inactive_patients_count(today, inactive_after_days=_INACTIVE_PATIENT_AFTER_DAYS)
        rows = await self.analytics_repo.list_inactive_patients(today, inactive_after_days=_INACTIVE_PATIENT_AFTER_DAYS)
        return InactivePatientsResponse(
            items=[
                InactivePatientItem(
                    patient_id=uuid.UUID(patient_id),
                    full_name=full_name,
                    last_appointment_at=last_appointment_at,
                    days_since_last_appointment=(datetime.now(timezone.utc) - last_appointment_at).days,
                )
                for patient_id, full_name, last_appointment_at in rows
            ],
            total_count=total_count,
            inactive_after_days=_INACTIVE_PATIENT_AFTER_DAYS,
        )

    async def get_early_churn_risk(self) -> EarlyChurnRiskResponse:
        """
        Raio-X da Receita, frente "Prevendo movimentos" — alerta
        ANTECIPADO de abandono, antes do paciente completar o piso fixo
        de 1 ano que o vira "inativo" de verdade (ver get_inactive_patients
        acima e DECISÃO completa em
        AnalyticsRepository.list_early_churn_risk_patients). Sem
        date_from/date_to de propósito (mesmo espírito de
        get_inactive_patients/get_health_score): é sempre "a partir de
        hoje", não uma janela de período.
        """
        today = date.today()
        total_count = await self.analytics_repo.count_early_churn_risk_patients(
            today, min_visits=_EARLY_CHURN_MIN_VISITS, gap_multiplier=_EARLY_CHURN_GAP_MULTIPLIER,
            inactive_after_days=_INACTIVE_PATIENT_AFTER_DAYS,
        )
        rows = await self.analytics_repo.list_early_churn_risk_patients(
            today, min_visits=_EARLY_CHURN_MIN_VISITS, gap_multiplier=_EARLY_CHURN_GAP_MULTIPLIER,
            inactive_after_days=_INACTIVE_PATIENT_AFTER_DAYS,
        )
        return EarlyChurnRiskResponse(
            items=[
                EarlyChurnRiskItem(
                    patient_id=uuid.UUID(row["patient_id"]),
                    full_name=row["patient_name"],
                    last_appointment_at=row["last_appointment_at"],
                    avg_interval_days=row["avg_interval_days"],
                    days_since_last=row["days_since_last"],
                )
                for row in rows
            ],
            total_count=total_count,
            gap_multiplier=_EARLY_CHURN_GAP_MULTIPLIER,
            inactive_after_days=_INACTIVE_PATIENT_AFTER_DAYS,
        )

    # "Junta Técnica Insighta" — CostEntry.category ("folha_fixa",
    # "comissao_repasse", "aluguel", "insumo", "outros" — ver
    # app/models/cost_entry.py) que representam custo FIXO (não varia
    # com volume de atendimento) para o benchmark "custo fixo saudável
    # fica até 60% da receita". "comissao_repasse"/"insumo" são
    # variáveis por definição (escalam com volume); "outros" fica de
    # fora de propósito — categoria ambígua, contar como fixo inflaria
    # o indicador sem base.
    _FIXED_COST_CATEGORIES = frozenset({"folha_fixa", "aluguel"})

    async def get_profitability(self, date_from: date, date_to: date) -> ProfitabilityResponse:
        """
        Raio-X da Receita, frente "Gestão eficiente": até esta rodada, o
        produto media ocupação de agenda e taxa de glosa por profissional
        SEPARADAMENTE (ver professional_utilization_rates/
        professional_denial_rates no motor de insights) — nunca receita
        por hora de agenda OCUPADA, a pergunta real por trás de "esse
        profissional está rendendo o que deveria". Cruza
        AnalyticsRepository.revenue_by_professional com CapacityService
        (o MESMO cálculo de minutos ocupados já usado em
        get_agenda_metrics), profissional por profissional.

        `by_procedure` é independente de profissional — ranking de mix
        de receita por código TUSS (ver
        AnalyticsRepository.revenue_by_procedure).

        Épico F3.1 do Plano Diretor ("Módulo de custos e margem real"):
        "ProfitabilityPanel hoje mostra receita por hora ocupada — não
        margem. Sem custo [...] toda conversa de 'rentabilidade' fica
        pela metade." Ver DECISÃO completa em
        app/sql/039_cost_entries.sql sobre o modelo de rateio (custo
        GERAL rateado proporcionalmente à receita de cada profissional
        no período; custo DIRETO de um profissional carregado 100%
        nele, sem diluir). `has_cost_data=False` (nenhum CostEntry
        lançado no período) deixa margem/custo em None em TODA a
        resposta — nunca inventa 0, que pareceria "sem custo nenhum"
        em vez de "sem dado de custo ainda".
        """
        billing = await self.reporting_repo.billing_summary(date_from, date_to)
        total_billed = billing["total_billed"]

        revenue_by_professional = await self.analytics_repo.revenue_by_professional(date_from, date_to)
        professionals_by_id = {str(p.id): p for p in await self.professional_repo.list_active()}

        by_professional: list[ProfessionalProfitabilityItem] = []
        for professional_id, revenue in revenue_by_professional.items():
            professional = professionals_by_id.get(professional_id)
            if professional is None:
                continue  # profissional inativo/removido — receita histórica sem dono pra exibir
            utilization = await self.capacity_service.get_utilization(professional.id, date_from, date_to)
            booked_minutes = utilization.booked_minutes
            revenue_per_hour = (revenue / (booked_minutes / 60)) if booked_minutes > 0 else None
            by_professional.append(
                ProfessionalProfitabilityItem(
                    professional_id=professional.id,
                    full_name=professional.full_name,
                    revenue=revenue,
                    booked_minutes=booked_minutes,
                    revenue_per_hour=revenue_per_hour,
                )
            )
        # Maior receita/hora primeiro; quem não tem agenda ocupada no
        # período (revenue_per_hour=None) vai pro final, nunca misturado
        # com quem tem valor real na frente.
        by_professional.sort(key=lambda item: (item.revenue_per_hour is None, -(item.revenue_per_hour or 0)))

        cost_entries = await self.cost_entry_repo.list_for_period(date_from=date_from, date_to=date_to)
        has_cost_data = bool(cost_entries)
        total_costs: float | None = None
        net_margin: float | None = None
        net_margin_pct: float | None = None
        fixed_cost_pct: float | None = None
        if has_cost_data:
            total_costs = round(sum(float(e.amount) for e in cost_entries), 2)
            net_margin = round(total_billed - total_costs, 2)
            if total_billed > 0:
                net_margin_pct = round(net_margin / total_billed * 100, 1)
                fixed_costs_total = sum(float(e.amount) for e in cost_entries if e.category in self._FIXED_COST_CATEGORIES)
                fixed_cost_pct = round(fixed_costs_total / total_billed * 100, 1)

            general_costs_total = sum(float(e.amount) for e in cost_entries if e.professional_id is None)
            direct_costs_by_professional: dict[str, float] = {}
            for e in cost_entries:
                if e.professional_id is not None:
                    key = str(e.professional_id)
                    direct_costs_by_professional[key] = direct_costs_by_professional.get(key, 0.0) + float(e.amount)

            # Rateio proporcional à receita — só entre quem TEM receita
            # faturada no período (a mesma lista `by_professional` já
            # filtrada acima). Um custo direto lançado pra um
            # profissional SEM receita no período ainda soma no
            # total_costs do tenant, mas não cria uma linha fantasma
            # aqui — decisão deliberada, não bug (ver DECISÃO no SQL).
            revenue_base_for_allocation = sum(item.revenue for item in by_professional)
            for item in by_professional:
                direct_cost = direct_costs_by_professional.get(str(item.professional_id), 0.0)
                allocated_general = (
                    (item.revenue / revenue_base_for_allocation) * general_costs_total
                    if revenue_base_for_allocation > 0 and general_costs_total > 0
                    else 0.0
                )
                allocated_cost = round(direct_cost + allocated_general, 2)
                item.allocated_cost = allocated_cost
                item.net_margin = round(item.revenue - allocated_cost, 2)
                item.margin_per_hour = (item.net_margin / (item.booked_minutes / 60)) if item.booked_minutes > 0 else None

        procedure_rows = await self.analytics_repo.revenue_by_procedure(date_from, date_to)
        by_procedure = [
            ProcedureProfitabilityItem(
                procedure_code=row["procedure_code"],
                procedure_name=row["procedure_name"],
                revenue=row["revenue"],
                billing_count=row["billing_count"],
                share_pct=(row["revenue"] / total_billed * 100) if total_billed > 0 else 0.0,
            )
            for row in procedure_rows
        ]

        return ProfitabilityResponse(
            period_start=date_from,
            period_end=date_to,
            total_billed=total_billed,
            by_professional=by_professional,
            by_procedure=by_procedure,
            has_cost_data=has_cost_data,
            net_margin_pct=net_margin_pct,
            fixed_cost_pct=fixed_cost_pct,
            total_costs=total_costs,
            net_margin=net_margin,
        )

    # Épico F3.4 do Plano Diretor ("Decisões de capital"): janela mais
    # longa que o resto do produto (6 meses, não 7/30 dias) de propósito
    # — receita/hora por especialidade é ruidosa numa janela curta, e
    # esta é a base de uma decisão de CONTRATAÇÃO, não um dashboard
    # operacional do dia a dia.
    _CAPITAL_DECISION_WINDOW_DAYS = 180
    # Menor que o usual (3, ver DATA_QUALITY_MIN_SAMPLE/MIN_APPEAL_HISTORY_SAMPLE)
    # de propósito: a população de profissionais de UMA especialidade
    # numa clínica pequena já é naturalmente pequena — exigir 3 tornaria
    # o fallback pra média da clínica quase sempre acionado, esvaziando
    # o propósito de filtrar por especialidade.
    _CAPITAL_DECISION_MIN_SAMPLE = 2

    async def get_capital_decision_base_data(
        self,
        specialty: str | None,
        *,
        belongs_to_organization: bool,
        sibling_monthly_revenues: list[float],
    ) -> CapitalDecisionBaseDataResponse:
        """
        Épico F3.4 do Plano Diretor ("Decisões de capital: contratar/
        expandir"). Metade "contratar" calculada aqui (receita/margem
        por hora por especialidade, reaproveitando get_profitability —
        ver DECISÃO no schema CapitalDecisionBaseDataResponse); metade
        "expandir" (`belongs_to_organization`/`sibling_monthly_revenues`)
        já vem PRONTA do endpoint, porque depende de
        OrganizationRepository, que só existe atrás de uma sessão
        cross-tenant (DbSessionNoTenant) — mesmo motivo de
        network_benchmark ser resolvido no endpoint em vez de aqui
        dentro (ver get_smart_insights/get_priority_queue).
        """
        # +2 dias de margem no fim da janela — mesmo motivo do resto do
        # produto quando cruza receita com agenda (ver `_window()` nos
        # testes de rentabilidade): um atendimento já faturado pode
        # estar agendado pra hoje/amanhã, e cortar a janela exatamente
        # em "hoje" descartaria a receita/hora desse profissional por um
        # detalhe de fuso, não por falta de dado real.
        date_to = date.today() + timedelta(days=2)
        date_from = date_to - timedelta(days=self._CAPITAL_DECISION_WINDOW_DAYS)
        profitability = await self.get_profitability(date_from, date_to)
        rated = [p for p in profitability.by_professional if p.revenue_per_hour is not None]

        professionals = await self.professional_repo.list_active()
        specialty_by_id = {str(p.id): (p.specialty or "").strip() for p in professionals}
        available_specialties = sorted({s for s in specialty_by_id.values() if s})

        normalized_requested = specialty.strip() if specialty and specialty.strip() else None
        used_fallback = False
        pool = rated
        if normalized_requested is not None:
            matching = [
                p
                for p in rated
                if specialty_by_id.get(str(p.professional_id), "").lower() == normalized_requested.lower()
            ]
            if len(matching) >= self._CAPITAL_DECISION_MIN_SAMPLE:
                pool = matching
            else:
                used_fallback = True  # cai pra média de toda a clínica (pool já é `rated`)

        sample_size = len(pool)
        avg_revenue_per_hour = (
            sum(p.revenue_per_hour for p in pool) / sample_size
            if sample_size >= self._CAPITAL_DECISION_MIN_SAMPLE
            else None
        )
        avg_margin_per_hour = None
        if profitability.has_cost_data:
            margin_pool = [p for p in pool if p.margin_per_hour is not None]
            if len(margin_pool) >= self._CAPITAL_DECISION_MIN_SAMPLE:
                avg_margin_per_hour = sum(p.margin_per_hour for p in margin_pool) / len(margin_pool)

        return CapitalDecisionBaseDataResponse(
            window_days=self._CAPITAL_DECISION_WINDOW_DAYS,
            period_start=date_from,
            period_end=date_to,
            available_specialties=available_specialties,
            specialty_requested=normalized_requested,
            used_fallback_clinic_wide=used_fallback,
            sample_size=sample_size,
            min_sample=self._CAPITAL_DECISION_MIN_SAMPLE,
            avg_revenue_per_hour=avg_revenue_per_hour,
            has_cost_data=profitability.has_cost_data,
            avg_margin_per_hour=avg_margin_per_hour,
            belongs_to_organization=belongs_to_organization,
            sibling_units_count=len(sibling_monthly_revenues),
            avg_monthly_revenue_per_unit=(
                sum(sibling_monthly_revenues) / len(sibling_monthly_revenues) if sibling_monthly_revenues else None
            ),
        )

    async def get_marketing_channels(self, date_from: date, date_to: date) -> MarketingChannelsResponse:
        """
        Raio-X da Receita, frente "Gestão eficiente": ROI de marketing
        existia só AGREGADO até esta rodada (gasto total vs. receita
        total atribuída, ver ReportDataService/_marketing_roi_insight)
        — um canal ótimo escondido atrás de um ruim nunca aparecia. Ver
        DECISÃO completa em ReportingRepository.marketing_performance_by_campaign.
        """
        rows = await self.reporting_repo.marketing_performance_by_campaign(date_from, date_to)
        return MarketingChannelsResponse(
            period_start=date_from,
            period_end=date_to,
            total_spend=sum(row["spend"] for row in rows),
            items=[MarketingChannelItem(**row) for row in rows],
        )

    async def get_recall_candidates(
        self, *, weekday: int | None = None, professional_id: str | None = None, limit: int = 15
    ) -> RecallCandidatesResponse:
        """
        Lista real por trás dos botões de ação de _weekday_drop_insight/
        _weekday_no_show_rate_insight (filtro `weekday`) e
        _capacity_drop_insight (filtro `professional_id`) — ver DECISÃO em
        AnalyticsRepository._recall_candidates_last_appointment. Exatamente
        um dos dois filtros é esperado por chamada (o endpoint valida
        isso); nenhum dos dois = candidatos sem filtro nenhum (não usado
        hoje pela Sala de Comando, mas a query aceita).
        """
        as_of = datetime.now(timezone.utc)
        professional_uuid = uuid.UUID(professional_id) if professional_id else None
        professional_name: str | None = None
        if professional_uuid is not None:
            professional = await self.professional_repo.get_by_id(professional_uuid)
            professional_name = professional.full_name if professional else None

        total_count = await self.analytics_repo.count_recall_candidates(
            as_of, weekday=weekday, professional_id=professional_uuid
        )
        rows = await self.analytics_repo.list_recall_candidates(
            as_of, weekday=weekday, professional_id=professional_uuid, limit=limit
        )
        return RecallCandidatesResponse(
            items=[
                RecallCandidateItem(
                    patient_id=uuid.UUID(patient_id),
                    full_name=full_name,
                    last_appointment_at=last_appointment_at,
                    days_since_last_appointment=(datetime.now(timezone.utc) - last_appointment_at).days,
                    last_professional_name=last_professional_name,
                )
                for patient_id, full_name, last_appointment_at, last_professional_name in rows
            ],
            total_count=total_count,
            weekday=weekday,
            professional_id=professional_uuid,
            professional_name=professional_name,
        )

    async def get_financial_hole_billings(
        self, date_from: date, date_to: date, *, limit: int = 15, offset: int = 0
    ) -> FinancialHoleBillingsResponse:
        """
        Lista real por trás do insight "Você está cobrando menos do que
        devia de alguns convênios" (ver DECISÃO em
        smart_insights_engine.py::_financial_hole_insight e
        AnalyticsRepository.list_financial_hole_billings) — achado do
        usuário: o card dizia QUANTO no total, mas não QUAIS contas
        corrigir. `total_hole_value` reaproveita financial_hole_total
        (mesma query-base do agregado) em vez de somar `items` na mão —
        os dois precisam bater mesmo quando a lista está paginada.

        Segundo achado do usuário, direto na tela: `limit`/`offset` são
        novos — antes esta lista só devolvia as 15 piores, sem jeito de
        ver o resto quando `total_count` era maior. `limit=15` continua
        o default (primeira página igual a antes).
        """
        total_count = await self.analytics_repo.count_financial_hole_billings(date_from, date_to)
        total_hole_value = await self.analytics_repo.financial_hole_total(date_from, date_to)
        rows = await self.analytics_repo.list_financial_hole_billings(date_from, date_to, limit=limit, offset=offset)
        return FinancialHoleBillingsResponse(
            period_start=date_from,
            period_end=date_to,
            items=[
                FinancialHoleBillingItem(
                    billing_id=uuid.UUID(row["billing_id"]),
                    patient_full_name=row["patient_full_name"],
                    procedure_label=row["procedure_label"],
                    insurance_plan_name=row["insurance_plan_name"],
                    charged_value=row["charged_value"],
                    agreed_price=row["agreed_price"],
                    hole_value=row["hole_value"],
                )
                for row in rows
            ],
            total_count=total_count,
            total_hole_value=total_hole_value,
            limit=limit,
            offset=offset,
        )

    async def get_agenda_revenue_forecast(self, date_from: date, date_to: date) -> AgendaRevenueForecastResponse:
        """
        Previsão de receita futura da agenda — pedido direto do usuário:
        "a receita da agenda... conseguimos tirar metade do faturamento
        futuro da clínica". Ver DECISÃO completa em
        AnalyticsRepository.agenda_revenue_forecast: 3 baldes separados
        (risco conhecido/ajustado, sem histórico ainda/sem ajuste, sem
        preço de contrato/fora da conta), nunca um único número que
        esconderia a incerteza real do dado.

        Diferente de todo o resto deste service (que olha para trás,
        `date_from`/`date_to` como janela PASSADA), aqui o período é
        FUTURO — ver `_default_future_period` no endpoint.
        """
        data = await self.analytics_repo.agenda_revenue_forecast(date_from, date_to)
        return AgendaRevenueForecastResponse(period_start=date_from, period_end=date_to, **data)

    async def get_denial_reason_confirmation(self) -> DenialReasonConfirmationResponse:
        """
        Camada 2 do plano de IA preditiva ("aprender com o histórico
        real de decisões" em vez de só regras fixas) — ver DECISÃO
        completa em AnalyticsRepository.denial_reason_confirmation_rates.
        Sem date_from/date_to de propósito (mesmo espírito de
        get_health_score/get_inactive_patients): usa todo o histórico já
        resolvido, não uma janela do dashboard.
        """
        data = await self.analytics_repo.denial_reason_confirmation_rates(
            min_sample=DENIAL_REASON_CONFIRMATION_MIN_SAMPLE
        )
        baseline_denied, baseline_total = data["baseline"]
        baseline_rate = (baseline_denied / baseline_total) if baseline_total > 0 else None

        items = [
            DenialReasonConfirmationItem(
                reason_code=reason_code,
                reason_label=describe_denial_reason(reason_code),
                sample_size=total,
                confirmed_denial_rate=denied / total,
            )
            for reason_code, (denied, total) in data["by_reason"].items()
        ]
        items.sort(key=lambda item: item.confirmed_denial_rate, reverse=True)

        return DenialReasonConfirmationResponse(
            baseline_sample_size=baseline_total,
            baseline_denial_rate=baseline_rate,
            items=items,
            min_sample=DENIAL_REASON_CONFIRMATION_MIN_SAMPLE,
        )

    async def get_data_quality_by_user(self, date_from: date, date_to: date) -> DataQualityResponse:
        """
        Épico F2.2 do Plano Diretor ("Qualidade de dado na origem") —
        quanto de cada atendente que lança atendimento já entra completo
        (CID + procedimento), a fonte mais comum de risco de glosa por
        dado ausente (ver DECISÃO completa em
        AnalyticsRepository.data_completeness_by_user). Ordenado do PIOR
        pro melhor — quem mais precisa de atenção/treinamento primeiro,
        mesmo critério de "pior caso primeiro" do resto do produto
        (Radar de Profissional, fila de ação priorizada).
        """
        rows = await self.analytics_repo.data_completeness_by_user(date_from, date_to, min_sample=DATA_QUALITY_MIN_SAMPLE)
        items = [
            DataQualityByUserItem(
                user_id=user_id,
                full_name=full_name,
                complete_count=complete,
                total_count=total,
                completion_rate=complete / total,
            )
            for user_id, full_name, complete, total in rows
        ]
        items.sort(key=lambda item: item.completion_rate)

        total_considered = sum(item.total_count for item in items)
        total_complete = sum(item.complete_count for item in items)
        overall_rate = (total_complete / total_considered) if total_considered > 0 else None

        return DataQualityResponse(
            items=items,
            overall_completion_rate=overall_rate,
            total_considered=total_considered,
            min_sample=DATA_QUALITY_MIN_SAMPLE,
        )

    async def get_product_roi(self) -> ProductRoiResponse:
        """
        Épico F4.4 do Plano Diretor ("Prova de ROI do próprio produto")
        — soma três componentes INDEPENDENTES e cumulativos (nunca uma
        janela de período): valor protegido pelo motor anti-glosa,
        valor recuperado em recursos de glosa ganhos, e ganho REAL
        medido em qualquer insight que um gestor fechou o ciclo (F1.2).
        Ver DECISÃO completa em ProductRoiResponse sobre por que os três
        continuam separados na resposta, não só um total.
        """
        protected_value, tracking_since = await self.reporting_repo.total_value_saved_all_time()
        recovered_value, recovered_count = await self.appeal_repo.sum_recovered_value()
        realized_value, realized_count = await self.insight_outcome_repo.sum_realized_delta()

        return ProductRoiResponse(
            protected_from_denial_value=protected_value,
            recovered_appeals_value=recovered_value,
            recovered_appeals_count=recovered_count,
            realized_insight_outcomes_value=realized_value,
            realized_insight_outcomes_count=realized_count,
            total_roi_value=protected_value + recovered_value + realized_value,
            tracking_since=tracking_since,
        )
