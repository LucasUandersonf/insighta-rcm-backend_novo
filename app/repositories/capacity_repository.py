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
        start = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
        end = datetime.combine(date_to, time.max, tzinfo=timezone.utc)
        stmt = select(func.coalesce(func.sum(Appointment.duration_minutes), 0)).where(
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
