"""
app/services/oportunidades_service.py

Serviço PRÓPRIO (mesmo espírito de NetworkBenchmarkService) porque
precisa de uma sessão SEM tenant (ver DbSessionNoTenant em
app/api/deps.py) — a aba Oportunidades da Sala de Comando 2.0.
"""
from uuid import UUID

from app.repositories.contract_price_benchmark_repository import (
    ContractPriceBenchmarkRepository,
    ContractPriceBenchmarkRow,
)
from app.schemas.analytics import OportunidadeItem, OportunidadesResponse

# Mesma janela de volume do Comparativo/Nota de Saúde — consistência
# entre os indicadores "de tendência" da Sala de Comando 2.0, nenhum
# deles segue o seletor de período de 7 dias da tela.
_VOLUME_WINDOW_DAYS = 90
# Piso menor que o do Comparativo (5): aqui o cohort é por
# convênio+procedimento, uma combinação muito mais específica — exigir 5
# clínicas com o MESMO procedimento MESMO convênio homologado deixaria a
# aba vazia na prática, mesmo em uma base com dezenas de clínicas. 3
# ainda garante que a mediana nunca é "a outra clínica", nunca uma
# comparação 1-para-1.
_MIN_COHORT = 3
# Gap mínimo para entrar no ranking — evita listar diferenças de
# centavos/arredondamento como se fossem "oportunidade real de
# renegociar". Mesmo espírito de outros pisos de ruído já usados no
# motor de insights (ver _SPIKE_THRESHOLD_PCT em smart_insights_engine.py).
_MIN_GAP_PCT = 0.02


def _to_item(row: ContractPriceBenchmarkRow) -> OportunidadeItem | None:
    gap_value = row.network_median_price - row.your_price
    if row.your_price <= 0 or gap_value <= 0:
        return None
    gap_pct = gap_value / row.your_price
    if gap_pct < _MIN_GAP_PCT:
        return None
    return OportunidadeItem(
        insurance_plan_id=row.insurance_plan_id,
        plan_display_name=row.plan_display_name,
        tuss_code=row.tuss_code,
        procedure_name=row.procedure_name,
        your_price=row.your_price,
        network_median_price=row.network_median_price,
        network_cohort_size=row.network_cohort_size,
        monthly_volume=row.monthly_volume,
        gap_value=gap_value,
        gap_pct=gap_pct,
        estimated_monthly_opportunity=gap_value * row.monthly_volume,
    )


class OportunidadesService:
    def __init__(self, repo: ContractPriceBenchmarkRepository):
        self.repo = repo

    async def get_oportunidades(self, tenant_id: UUID) -> OportunidadesResponse:
        rows = await self.repo.list_benchmark(
            tenant_id, min_cohort=_MIN_COHORT, volume_window_days=_VOLUME_WINDOW_DAYS
        )
        items = [item for row in rows if (item := _to_item(row)) is not None]
        # Maior oportunidade financeira primeiro — é a pergunta que a
        # tela responde ("por onde eu começo?"), não ordem alfabética
        # nem ordem de retorno da query.
        items.sort(key=lambda i: i.estimated_monthly_opportunity, reverse=True)
        return OportunidadesResponse(items=items, window_days=_VOLUME_WINDOW_DAYS)
