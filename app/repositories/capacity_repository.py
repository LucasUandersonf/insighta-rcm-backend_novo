"""
app/repositories/capacity_repository.py

DECISÃO — agregação feita no Postgres (SUM), não em Python
-------------------------------------------------------------------------
Somar duration_minutes de milhares de appointments é exatamente o tipo
de trabalho que o banco faz melhor que carregar tudo para a aplicação e
somar em um loop Python — evita trazer linha por linha pela rede só para
descartar tudo, menos um número.
"""
import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment


class CapacityRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def booked_minutes(self, professional_id: uuid.UUID, date_from: date, date_to: date) -> int:
        """
        DECISÃO — achado de auditoria (revisão forte da Sala de Comando):
        todo agendamento vindo da ingestão de Faturamento (a origem
        principal de dado do produto) NUNCA tem duration_minutes
        preenchido — normalize_row (normalization_service.py) nem
        referencia esse campo, porque RawBillingRow não carrega duração
        nenhuma (só o Template Agenda tem essa coluna, e mesmo lá é
        opcional). Sem tratamento, SUM(duration_minutes) ignora
        silenciosamente toda linha com duração NULL — na prática,
        zerava booked_minutes/utilization_rate pra qualquer tenant cujo
        dado vem só de Faturamento, e isso em cascata fazia
        total_idle_minutes/estimated_revenue_lost_to_idle_capacity
        enxergarem 100% da capacidade instalada como ociosa.

        Mesmo raciocínio já documentado em
        capacity_service.estimate_idle_capacity_revenue_lost (duração
        OBSERVADA, nunca uma constante inventada): quando a linha não
        tem duração própria, usa a duração média observada em QUALQUER
        outro agendamento do mesmo tenant que tenha duração conhecida
        (RLS já restringe a subquery ao tenant certo, mesma sessão).
        Sem nenhum agendamento com duração conhecida em lugar nenhum do
        tenant, não há como estimar honestamente — a linha continua
        contribuindo 0, o piso realista de "sem sinal nenhum pra
        estimar" (nunca um número inventado do nada).
        """
        start = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
        end = datetime.combine(date_to, time.max, tzinfo=timezone.utc)
        fallback_duration = (
            select(func.avg(Appointment.duration_minutes))
            .where(Appointment.duration_minutes.is_not(None), Appointment.status != "cancelled")
            .scalar_subquery()
        )
        stmt = select(func.coalesce(func.sum(func.coalesce(Appointment.duration_minutes, fallback_duration)), 0)).where(
            Appointment.professional_id == professional_id,
            Appointment.scheduled_at >= start,
            Appointment.scheduled_at <= end,
            Appointment.status != "cancelled",  # cancelado não ocupou a agenda de fato
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def status_counts(self, professional_id: uuid.UUID, date_from: date, date_to: date) -> dict[str, int]:
        start = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
        end = datetime.combine(date_to, time.max, tzinfo=timezone.utc)
        stmt = (
            select(Appointment.status, func.count())
            .where(
                Appointment.professional_id == professional_id,
                Appointment.scheduled_at >= start,
                Appointment.scheduled_at <= end,
            )
            .group_by(Appointment.status)
        )
        result = await self.session.execute(stmt)
        return {status: count for status, count in result.all()}
