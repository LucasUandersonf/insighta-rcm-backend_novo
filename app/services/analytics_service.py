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
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.repositories.analytics_repository import AnalyticsRepository
from app.repositories.capacity_repository import CapacityRepository
from app.repositories.denial_appeal_repository import DenialAppealRepository
from app.repositories.health_score_snapshot_repository import HealthScoreSnapshotRepository
from app.repositories.professional_availability_repository import ProfessionalAvailabilityRepository
from app.repositories.professional_repository import ProfessionalRepository
from app.repositories.reporting_repository import ReportingRepository
from app.repositories.tenant_repository import TenantRepository
from app.schemas.analytics import (
    AgendaMetricsResponse,
    ContractUtilizationItem,
    ContractUtilizationResponse,
    DenialRiskDistributionItem,
    DenialRiskDistributionResponse,
    ExecutiveSummaryResponse,
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
    PlanLossItem,
    PlanLossRankingResponse,
    ProfessionalCapacityMetric,
    RecallCandidateItem,
    RecallCandidatesResponse,
    SmartInsightResponse,
    SmartInsightsResponse,
    UpcomingRiskAppointmentItem,
    WeekdayBucket,
    WeekdayNoShowRateBucket,
)
from app.services.capacity_service import CapacityService, estimate_idle_capacity_revenue_lost
from app.services.health_score_engine import compute_health_score
from app.services.smart_insights_engine import (
    DenialReasonCount,
    InsightsPeriodInput,
    build_network_comparativo_insight,
    describe_denial_reason,
    describe_worst_no_show_weekday,
    generate_insights,
    is_true_denial_risk_reason,
)

# Janela FIXA da Nota de Saúde Financeira — de propósito independente do
# seletor de período da Sala de Comando (que pode ser 7 dias). Um score
# de "tendência de saúde da clínica" que pula toda vez que o usuário
# muda o filtro de 7 para 30 dias pareceria ruído, não sinal — e 90 dias
# dá amostra mínima razoável para o componente de recurso de glosa
# (resolução de recurso é lenta, poucos por semana).
_HEALTH_SCORE_WINDOW_DAYS = 90

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

# Janela de alerta de prazo de recurso: "vencendo em breve" — mesmo
# princípio de MIN_SAMPLE_SIZE/thresholds em smart_insights_engine.py,
# um número fixo e nomeado em vez de mágico espalhado pelo código.
APPEAL_DEADLINE_ALERT_HORIZON_DAYS = 5

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
    ):
        self.analytics_repo = analytics_repo
        self.reporting_repo = reporting_repo
        self.professional_repo = professional_repo
        self.appeal_repo = appeal_repo
        self.tenant_repo = tenant_repo
        self.availability_repo = availability_repo
        self.health_score_snapshot_repo = health_score_snapshot_repo
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
        breakdown = await self.analytics_repo.denial_risk_count_breakdown(date_from, date_to)
        items = [DenialRiskDistributionItem(level=level, count=count) for level, count in breakdown.items()]
        return DenialRiskDistributionResponse(
            period_start=date_from, period_end=date_to, items=items, total_reviewed=sum(breakdown.values())
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
    ) -> InsightsPeriodInput:
        # appeals_due_soon é passado de fora, não recalculado aqui: é um
        # estado "AGORA" (prazo vencendo hoje), não algo que faça sentido
        # perguntar de novo para o "período anterior" — comparar contra
        # si mesmo sempre daria delta zero. Só o período ATUAL recebe o
        # valor real; o anterior fica no default 0 do dataclass.
        billing = await self.reporting_repo.billing_summary(date_from, date_to)
        financial_hole = await self.analytics_repo.financial_hole_total(date_from, date_to)
        payment_gap = await self.analytics_repo.payment_gap_total(date_from, date_to)
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

        # Meta anual (Auditoria Go-Live, terceiro exemplo do briefing de
        # redesenho) — só calculado para o período ATUAL, nunca para o
        # anterior (não existe "meta do período anterior", ver
        # _AnnualGoalContext). tenant.annual_revenue_goal é lido direto do
        # Tenant (tabela sem RLS — mesmo motivo de TenantRepository já
        # existir separado, ver seu docstring), nunca calculado sozinho.
        today = date.today()
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
        )
        previous_input = await self._period_insights_input(previous.start, previous.end)
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

        insights = generate_insights(
            current_input,
            previous_input,
            estimated_revenue_at_risk,
            estimated_idle_capacity_revenue_lost,
            extra_insights=extra_insights,
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

    async def get_health_score(self) -> HealthScoreResponse:
        """
        Nota de Saúde Financeira — ver DECISÃO completa em
        health_score_engine.py (regras determinísticas, componente sem
        amostra é excluído, nunca vira zero). Janela sempre fixa (ver
        _HEALTH_SCORE_WINDOW_DAYS acima), nunca a do seletor de período.
        """
        today = date.today()
        window_start_date = today - timedelta(days=_HEALTH_SCORE_WINDOW_DAYS)
        window_start_dt = datetime.combine(window_start_date, datetime.min.time(), tzinfo=timezone.utc)

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

        result = compute_health_score(
            denial_risk_pct=denial_risk_pct,
            no_show_count=no_show_count,
            no_show_total=no_show_total,
            appeal_deferred_count=appeal_counts["deferido"],
            appeal_indeferido_count=appeal_counts["indeferido"],
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

    async def get_financial_hole_billings(self, date_from: date, date_to: date) -> FinancialHoleBillingsResponse:
        """
        Lista real por trás do insight "Você está cobrando menos do que
        devia de alguns convênios" (ver DECISÃO em
        smart_insights_engine.py::_financial_hole_insight e
        AnalyticsRepository.list_financial_hole_billings) — achado do
        usuário: o card dizia QUANTO no total, mas não QUAIS contas
        corrigir. `total_hole_value` reaproveita financial_hole_total
        (mesma query-base do agregado) em vez de somar `items` na mão —
        os dois precisam bater mesmo quando a lista é truncada.
        """
        total_count = await self.analytics_repo.count_financial_hole_billings(date_from, date_to)
        total_hole_value = await self.analytics_repo.financial_hole_total(date_from, date_to)
        rows = await self.analytics_repo.list_financial_hole_billings(date_from, date_to)
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
        )
