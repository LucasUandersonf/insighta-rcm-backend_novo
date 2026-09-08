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
