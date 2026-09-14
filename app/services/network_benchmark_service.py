"""
app/services/network_benchmark_service.py

Serviço PRÓPRIO (não dentro de AnalyticsService) porque este é o único
ponto da Sala de Comando que precisa de uma sessão SEM tenant (ver
DbSessionNoTenant em app/api/deps.py) — misturar isso dentro de
AnalyticsService, que hoje sempre recebe uma sessão tenant-aware,
tornaria fácil um dev futuro esquecer qual sessão está em uso onde.
Mesmo espírito de separação de PlatformReportingService.
"""
from uuid import UUID

from app.repositories.network_benchmark_repository import NetworkBenchmarkRepository
from app.schemas.analytics import NetworkBenchmarkMetric, NetworkBenchmarkResponse
from app.schemas.tenant import AnnualGoalSuggestionResponse

# Épico F3.3 do Plano Diretor ("Metas e cenários orientados a dados") —
# mesmo piso documentado em app/sql/041_network_revenue_growth_benchmark.sql,
# repetido aqui só para o parâmetro default da função SQL.
_REVENUE_GROWTH_MIN_COHORT = 5

# Mesma janela da Nota de Saúde Financeira (ver _HEALTH_SCORE_WINDOW_DAYS
# em analytics_service.py) — consistência entre os dois indicadores de
# tendência da Sala de Comando 2.0, nenhum dos dois segue o seletor de
# período de 7 dias da tela.
_WINDOW_DAYS = 90
# Mesmo piso documentado em app/sql/032_network_benchmark.sql — repetido
# aqui só para o schema de resposta saber o valor default sem precisar
# fazer round-trip nenhum; a função SQL aplica o piso de verdade, este
# valor aqui é só o default do parâmetro passado a ela.
_MIN_COHORT = 5


class NetworkBenchmarkService:
    def __init__(self, repo: NetworkBenchmarkRepository):
        self.repo = repo

    async def get_benchmark(self, tenant_id: UUID) -> NetworkBenchmarkResponse:
        row = await self.repo.get_benchmark(tenant_id, window_days=_WINDOW_DAYS, min_cohort=_MIN_COHORT)
        return NetworkBenchmarkResponse(
            metrics=[
                NetworkBenchmarkMetric(
                    key="denial",
                    label="Taxa de glosa",
                    your_rate=row.your_denial_rate,
                    your_sample=row.your_denial_sample,
                    network_median=row.network_denial_median,
                    cohort_size=row.denial_cohort_size,
                ),
                NetworkBenchmarkMetric(
                    key="no_show",
                    label="Taxa de falta",
                    your_rate=row.your_no_show_rate,
                    your_sample=row.your_no_show_sample,
                    network_median=row.network_no_show_median,
                    cohort_size=row.no_show_cohort_size,
                ),
            ],
            window_days=_WINDOW_DAYS,
        )

    async def get_annual_goal_suggestion(self, tenant_id: UUID) -> AnnualGoalSuggestionResponse:
        """
        Épico F3.3 do Plano Diretor ("Metas e cenários orientados a
        dados") — "meta anual sugerida (crescimento histórico +
        percentil de rede)". Projeta o MESMO faturamento base (seus
        últimos 12 meses) por duas taxas de crescimento independentes —
        ver DECISÃO completa em
        app/sql/041_network_revenue_growth_benchmark.sql sobre por que
        nunca uma média escondida entre elas.
        """
        row = await self.repo.get_revenue_growth(tenant_id, min_cohort=_REVENUE_GROWTH_MIN_COHORT)

        own_trend_suggested_goal = None
        if row.your_growth_rate is not None and row.your_trailing_12mo_total > 0:
            own_trend_suggested_goal = row.your_trailing_12mo_total * (1 + row.your_growth_rate)

        network_pace_suggested_goal = None
        if row.network_growth_median is not None and row.your_trailing_12mo_total > 0:
            network_pace_suggested_goal = row.your_trailing_12mo_total * (1 + row.network_growth_median)

        return AnnualGoalSuggestionResponse(
            trailing_12_months_total=row.your_trailing_12mo_total,
            own_growth_rate=row.your_growth_rate,
            own_trend_suggested_goal=own_trend_suggested_goal,
            network_growth_median=row.network_growth_median,
            network_pace_suggested_goal=network_pace_suggested_goal,
            network_cohort_size=row.growth_cohort_size,
        )
