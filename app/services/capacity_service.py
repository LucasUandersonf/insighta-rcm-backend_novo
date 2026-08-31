"""
app/services/capacity_service.py

Calcula a taxa de utilização de um profissional em um período: quanto da
capacidade TEÓRICA instalada (grade semanal recorrente em
professional_availability) foi de fato convertida em minutos agendados
(appointments.duration_minutes).

DECISÃO — cálculo de minutos disponíveis é puro Python (sem SQL), minutos
ocupados vêm agregados do banco (CapacityRepository)
-------------------------------------------------------------------------
A grade de disponibilidade de um profissional é pequena (algumas linhas
por profissional — "seg-sex 8h-12h e 14h-18h"), então iterar os dias do
período em Python e somar os blocos que batem com cada weekday é simples,
legível e não precisa de SQL genérico o bastante para lidar com "quantas
vezes cada dia da semana ocorre entre duas datas". Já os minutos
OCUPADOS vêm de milhares de linhas de appointments — isso sim compensa
agregar no banco (ver CapacityRepository.booked_minutes).

DECISÃO — o que conta como "ocupado" para fins de utilização
-------------------------------------------------------------------------
Todo appointment não cancelado ocupa a agenda, INCLUSIVE no-show — o
profissional reservou aquele horário e não pôde usá-lo para outro
paciente, então o slot foi consumido do ponto de vista de capacidade,
mesmo que não tenha gerado receita. Por isso reportamos utilization_rate
(ocupação bruta da agenda) e no_show_rate (fração disso que não veio)
como duas métricas separadas — uma mede capacidade consumida, a outra
mede receita perdida dentro da capacidade já consumida. Confundir as
duas seria um erro de diagnóstico: uma agenda 90% ocupada com 40% de
no-show tem um problema de ausência de paciente, não de falta de agenda
disponível — soluções diferentes para cada caso.
"""
from dataclasses import dataclass
from datetime import date, timedelta

from app.repositories.capacity_repository import CapacityRepository
from app.repositories.professional_availability_repository import ProfessionalAvailabilityRepository


@dataclass
class UtilizationResult:
    available_minutes: int
    booked_minutes: int
    utilization_rate: float  # 0.0 a 1.0 (ou >1.0 se overbooking — sinaliza um problema de cadastro de duração)
    no_show_rate: float
    total_appointments: int
    status_breakdown: dict[str, int]


class CapacityService:
    def __init__(self, availability_repo: ProfessionalAvailabilityRepository, capacity_repo: CapacityRepository):
        self.availability_repo = availability_repo
        self.capacity_repo = capacity_repo

    async def _available_minutes(self, professional_id, date_from: date, date_to: date) -> int:
        blocks = await self.availability_repo.list_by_professional(professional_id)
        if not blocks:
            return 0

        # Agrupa os blocos por weekday para não recalcular a lista inteira
        # a cada dia do período.
        minutes_by_weekday: dict[int, int] = {}
        for block in blocks:
            minutes = (block.end_time.hour * 60 + block.end_time.minute) - (
                block.start_time.hour * 60 + block.start_time.minute
            )
            minutes_by_weekday[block.weekday] = minutes_by_weekday.get(block.weekday, 0) + minutes

        total = 0
        current = date_from
        while current <= date_to:
            # Python: Monday=0..Sunday=6. Nosso schema usa 0=domingo..6=sábado
            # (convenção mais comum em agenda de clínica no Brasil) — conversão aqui.
            weekday_pt = (current.weekday() + 1) % 7
            total += minutes_by_weekday.get(weekday_pt, 0)
            current += timedelta(days=1)
        return total

    async def get_utilization(self, professional_id, date_from: date, date_to: date) -> UtilizationResult:
        available = await self._available_minutes(professional_id, date_from, date_to)
        booked = await self.capacity_repo.booked_minutes(professional_id, date_from, date_to)
        status_breakdown = await self.capacity_repo.status_counts(professional_id, date_from, date_to)

        total_appointments = sum(status_breakdown.values())
        no_show_count = status_breakdown.get("no_show", 0)

        return UtilizationResult(
            available_minutes=available,
            booked_minutes=booked,
            utilization_rate=(booked / available) if available > 0 else 0.0,
            no_show_rate=(no_show_count / total_appointments) if total_appointments > 0 else 0.0,
            total_appointments=total_appointments,
            status_breakdown=status_breakdown,
        )
