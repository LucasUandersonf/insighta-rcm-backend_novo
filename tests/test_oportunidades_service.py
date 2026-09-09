"""
tests/test_oportunidades_service.py

Testa a lógica pura de app/services/oportunidades_service.py (filtro de
gap mínimo, descarte de preço no nível/acima da rede, ordenação por
oportunidade financeira) sem precisar de banco — o SQL cross-tenant em
si já é provado em tests/integration/test_oportunidades.py.
"""
import uuid

import pytest

from app.repositories.contract_price_benchmark_repository import ContractPriceBenchmarkRow
from app.services.oportunidades_service import OportunidadesService


def _row(**overrides) -> ContractPriceBenchmarkRow:
    defaults = dict(
        insurance_plan_id=uuid.uuid4(),
        plan_display_name="Convênio Teste",
        tuss_code="10101012",
        procedure_name="Consulta em consultório",
        your_price=100.0,
        network_median_price=120.0,
        network_cohort_size=3,
        monthly_volume=10.0,
    )
    defaults.update(overrides)
    return ContractPriceBenchmarkRow(**defaults)


class _FakeRepo:
    def __init__(self, rows: list[ContractPriceBenchmarkRow]):
        self._rows = rows

    async def list_benchmark(self, tenant_id, *, min_cohort, volume_window_days):
        return self._rows


@pytest.mark.asyncio
async def test_only_lists_items_where_your_price_is_below_network_median():
    rows = [
        _row(tuss_code="A", your_price=100.0, network_median_price=120.0),  # 20% abaixo -> oportunidade
        _row(tuss_code="B", your_price=150.0, network_median_price=120.0),  # já acima da rede -> não é oportunidade
        _row(tuss_code="C", your_price=120.0, network_median_price=120.0),  # exatamente igual -> não é oportunidade
    ]
    service = OportunidadesService(_FakeRepo(rows))

    result = await service.get_oportunidades(uuid.uuid4())

    assert [item.tuss_code for item in result.items] == ["A"]


@pytest.mark.asyncio
async def test_ignores_gap_below_minimum_percentage_as_rounding_noise():
    rows = [_row(tuss_code="A", your_price=100.0, network_median_price=100.5)]  # 0.5% — ruído
    service = OportunidadesService(_FakeRepo(rows))

    result = await service.get_oportunidades(uuid.uuid4())

    assert result.items == []


@pytest.mark.asyncio
async def test_estimated_opportunity_is_gap_times_monthly_volume():
    rows = [_row(tuss_code="A", your_price=100.0, network_median_price=150.0, monthly_volume=8.0)]
    service = OportunidadesService(_FakeRepo(rows))

    result = await service.get_oportunidades(uuid.uuid4())

    item = result.items[0]
    assert item.gap_value == 50.0
    assert item.gap_pct == pytest.approx(0.5)
    assert item.estimated_monthly_opportunity == 400.0


@pytest.mark.asyncio
async def test_ranking_orders_by_estimated_monthly_opportunity_descending():
    rows = [
        _row(tuss_code="pequena", your_price=100.0, network_median_price=110.0, monthly_volume=1.0),  # 10
        _row(tuss_code="grande", your_price=100.0, network_median_price=200.0, monthly_volume=5.0),  # 500
        _row(tuss_code="media", your_price=100.0, network_median_price=150.0, monthly_volume=2.0),  # 100
    ]
    service = OportunidadesService(_FakeRepo(rows))

    result = await service.get_oportunidades(uuid.uuid4())

    assert [item.tuss_code for item in result.items] == ["grande", "media", "pequena"]


@pytest.mark.asyncio
async def test_zero_volume_still_lists_the_gap_but_with_zero_opportunity():
    """Amostra insuficiente de faturamento recente não deveria escondar
    a oportunidade de preço em si — só zera a estimativa financeira
    (honesto: "não sei quanto vale por mês", não "não existe")."""
    rows = [_row(tuss_code="A", your_price=100.0, network_median_price=150.0, monthly_volume=0.0)]
    service = OportunidadesService(_FakeRepo(rows))

    result = await service.get_oportunidades(uuid.uuid4())

    assert result.items[0].estimated_monthly_opportunity == 0.0
