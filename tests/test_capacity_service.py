"""
tests/test_capacity_service.py

Testa CapacityService sem banco real: os repositórios são substituídos
por dublês simples (duck typing — só precisam expor os métodos async que
o service chama). O mesmo princípio de sempre: a camada service é
testável isolada da infraestrutura de dados.
"""
import asyncio
from datetime import date, time

import pytest

from app.models.professional_availability import ProfessionalAvailability
from app.services.capacity_service import CapacityService, estimate_idle_capacity_revenue_lost


class _FakeAvailabilityRepo:
    def __init__(self, blocks: list[ProfessionalAvailability]):
        self._blocks = blocks

    async def list_by_professional(self, professional_id):
        return self._blocks


class _FakeCapacityRepo:
    def __init__(self, booked_minutes: int, status_counts: dict[str, int]):
        self._booked_minutes = booked_minutes
        self._status_counts = status_counts

    async def booked_minutes(self, professional_id, date_from, date_to):
        return self._booked_minutes

    async def status_counts(self, professional_id, date_from, date_to):
        return self._status_counts


def _block(weekday: int, start: str, end: str) -> ProfessionalAvailability:
    h1, m1 = map(int, start.split(":"))
    h2, m2 = map(int, end.split(":"))
    return ProfessionalAvailability(weekday=weekday, start_time=time(h1, m1), end_time=time(h2, m2))


@pytest.mark.asyncio
async def test_utilization_rate_for_a_full_business_week():
    # Grade: seg-sex (weekday 1 a 5 na convenção 0=domingo), 8h-12h e 14h-18h = 480 min/dia
    blocks = [_block(wd, "08:00", "12:00") for wd in range(1, 6)] + [_block(wd, "14:00", "18:00") for wd in range(1, 6)]
    availability_repo = _FakeAvailabilityRepo(blocks)
    # 5 dias úteis * 480 min = 2400 min disponíveis; simulamos 1200 min ocupados (50%)
    capacity_repo = _FakeCapacityRepo(booked_minutes=1200, status_counts={"completed": 18, "no_show": 2})

    service = CapacityService(availability_repo, capacity_repo)
    result = await service.get_utilization(
        professional_id="dummy", date_from=date(2026, 8, 24), date_to=date(2026, 8, 28)  # segunda a sexta
    )

    assert result.available_minutes == 2400
    assert result.booked_minutes == 1200
    assert result.utilization_rate == 0.5
    assert result.total_appointments == 20
    assert result.no_show_rate == 0.1


@pytest.mark.asyncio
async def test_no_availability_configured_returns_zero_without_dividing_by_zero():
    service = CapacityService(_FakeAvailabilityRepo([]), _FakeCapacityRepo(booked_minutes=0, status_counts={}))

    result = await service.get_utilization("dummy", date(2026, 8, 24), date(2026, 8, 28))

    assert result.available_minutes == 0
    assert result.utilization_rate == 0.0
    assert result.no_show_rate == 0.0


# ---------------------------------------------------------------------
# estimate_idle_capacity_revenue_lost — função pura, mesmo espírito de
# report_calculations.py: nenhuma dependência de banco/asyncio.
# ---------------------------------------------------------------------


def test_idle_minutes_converted_to_equivalent_appointments_times_ticket():
    # 1200 min ocupados / 20 consultas = 60 min/consulta em média;
    # 600 min ociosos -> 10 consultas equivalentes * R$ 150 = R$ 1500.
    value = estimate_idle_capacity_revenue_lost(
        idle_minutes=600, booked_minutes=1200, total_appointments=20, avg_charged_value=150.0
    )
    assert value == 1500.0


def test_zero_idle_minutes_is_zero_loss():
    value = estimate_idle_capacity_revenue_lost(
        idle_minutes=0, booked_minutes=1200, total_appointments=20, avg_charged_value=150.0
    )
    assert value == 0.0


def test_no_booked_appointments_cannot_infer_average_duration():
    # Sem nenhuma consulta realizada no período, não há duração média
    # observada para converter minutos ociosos em "consultas equivalentes"
    # — retorna 0.0 em vez de dividir por zero ou inventar uma duração.
    value = estimate_idle_capacity_revenue_lost(
        idle_minutes=1000, booked_minutes=0, total_appointments=0, avg_charged_value=150.0
    )
    assert value == 0.0


def test_negative_idle_minutes_is_treated_as_no_loss():
    # Não deveria acontecer (overbooking apareceria como idle=0, nunca
    # negativo — quem faz o clamp é o chamador), mas a função não deve
    # devolver um valor negativo mesmo se receber um número negativo.
    value = estimate_idle_capacity_revenue_lost(
        idle_minutes=-100, booked_minutes=1200, total_appointments=20, avg_charged_value=150.0
    )
    assert value == 0.0


if __name__ == "__main__":
    # Execução manual sem pytest instalado (ambiente sem acesso à rede) —
    # roda os dois testes diretamente com asyncio, só para validação local.
    asyncio.run(test_utilization_rate_for_a_full_business_week())
    asyncio.run(test_no_availability_configured_returns_zero_without_dividing_by_zero())
    test_idle_minutes_converted_to_equivalent_appointments_times_ticket()
    test_zero_idle_minutes_is_zero_loss()
    test_no_booked_appointments_cannot_infer_average_duration()
    test_negative_idle_minutes_is_treated_as_no_loss()
    print("Testes de capacity_service passaram.")
