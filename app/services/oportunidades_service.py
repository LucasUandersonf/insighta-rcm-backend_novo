"""
app/services/oportunidades_service.py

Serviço PRÓPRIO (mesmo espírito de NetworkBenchmarkService) porque
precisa de uma sessão SEM tenant (ver DbSessionNoTenant em
app/api/deps.py) — a aba Oportunidades da Sala de Comando 2.0.
"""
from datetime import date
from uuid import UUID

from app.repositories.contract_price_benchmark_repository import (
    ContractPriceBenchmarkRepository,
    ContractPriceBenchmarkRow,
)
from app.repositories.contract_repository import ContractRepository
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
# "Junta Técnica Insighta" (reavaliação de mercado da Sala de Comando):
# achamos a citação que mais valida a aba Oportunidades em toda a
# pesquisa — negociar reajuste de convênio "sozinho, sem base técnica,
# costuma terminar em aceita ou troca", e um corretor especializado
# recomenda começar a preparação 120 DIAS antes do aniversário do
# contrato. Mais largo de propósito que
# CONTRACT_EXPIRING_ALERT_HORIZON_DAYS (30, em analytics_service.py) —
# aquele é o alerta "urgente, vai vencer" do Diagnóstico; este é a janela
# "comece a se preparar" ligada ao ranking de oportunidade, um uso
# diferente do mesmo dado.
CONTRACT_RENEWAL_PREP_HORIZON_DAYS = 120


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
    def __init__(self, repo: ContractPriceBenchmarkRepository, contract_repo: ContractRepository):
        self.repo = repo
        # Tenant-aware (ao contrário de `repo` acima) — precisa ler
        # core.contracts DESTE tenant (RLS), não cruzar entre clínicas.
        # Ver DECISÃO completa em CONTRACT_RENEWAL_PREP_HORIZON_DAYS.
        self.contract_repo = contract_repo

    async def get_oportunidades(self, tenant_id: UUID) -> OportunidadesResponse:
        rows = await self.repo.list_benchmark(
            tenant_id, min_cohort=_MIN_COHORT, volume_window_days=_VOLUME_WINDOW_DAYS
        )
        items = [item for row in rows if (item := _to_item(row)) is not None]
        # Maior oportunidade financeira primeiro — é a pergunta que a
        # tela responde ("por onde eu começo?"), não ordem alfabética
        # nem ordem de retorno da query.
        items.sort(key=lambda i: i.estimated_monthly_opportunity, reverse=True)

        if items:
            today = date.today()
            expiring_rows = await self.contract_repo.expiring_without_renewal_summary(
                today, CONTRACT_RENEWAL_PREP_HORIZON_DAYS
            )
            # Ordenado por valid_until ascendente (ver DECISÃO no
            # repositório) — o primeiro encontrado por convênio já é o
            # mais próximo de vencer, então nunca sobrescrevemos.
            renewal_by_plan: dict[UUID, date] = {}
            for row in expiring_rows:
                renewal_by_plan.setdefault(row["insurance_plan_id"], row["valid_until"])
            for item in items:
                valid_until = renewal_by_plan.get(item.insurance_plan_id)
                if valid_until is not None:
                    item.contract_valid_until = valid_until
                    item.days_until_contract_renewal = (valid_until - today).days

        return OportunidadesResponse(items=items, window_days=_VOLUME_WINDOW_DAYS)
