import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment


class AppointmentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_by_patient(self, patient_id: uuid.UUID) -> list[Appointment]:
        stmt = select(Appointment).where(Appointment.patient_id == patient_id).order_by(Appointment.scheduled_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, appointment_id: uuid.UUID) -> Appointment | None:
        stmt = select(Appointment).where(Appointment.id == appointment_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_past_by_patient(self, patient_id: uuid.UUID, before: datetime) -> list[Appointment]:
        """
        Histórico de atendimentos JÁ OCORRIDOS (completed/no_show) do
        paciente, anteriores ao horário do agendamento sendo criado —
        entrada do no_show_risk_engine. 'before' existe para nunca usar
        um atendimento futuro (ou o próprio que está sendo criado) como
        parte do histórico que prevê ele mesmo.
        """
        stmt = select(Appointment).where(
            Appointment.patient_id == patient_id,
            Appointment.scheduled_at < before,
            Appointment.status.in_(("completed", "no_show")),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add(self, appointment: Appointment) -> Appointment:
        self.session.add(appointment)
        await self.session.flush()
        return appointment

    async def save(self, appointment: Appointment) -> Appointment:
        await self.session.flush()
        return appointment
