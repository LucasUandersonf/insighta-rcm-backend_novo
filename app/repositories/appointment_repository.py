import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.patient import Patient


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

    async def get_by_external_id(self, tenant_id: uuid.UUID, external_id: str) -> Appointment | None:
        """Usado pela normalização do template de Agenda (ver
        app/sql/019_agenda_ingestion.sql) para UPSERT: o mesmo
        agendamento é tipicamente reexportado várias vezes conforme seu
        status muda (agendado -> confirmado -> atendido/faltou) — sem
        casar pelo external_id do sistema de origem, cada reimportação
        criaria um agendamento duplicado."""
        stmt = select(Appointment).where(Appointment.tenant_id == tenant_id, Appointment.external_id == external_id)
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

    async def list_by_date_range_paginated(
        self, date_from: datetime, date_to: datetime, *, limit: int, offset: int
    ) -> tuple[list[tuple[Appointment, str]], int]:
        """
        Peça que faltava depois do Achado 12 da Auditoria de Templates e
        Insights: os insights de canal de agendamento/motivo de
        cancelamento (ver smart_insights_engine.py) apontavam o problema
        em AGREGADO, mas não existia nenhum endpoint que listasse
        agendamentos individuais de um período — só `list_by_patient`
        (um paciente específico) e `get_by_id` (um registro). Devolve
        (Appointment, nome_do_paciente) já resolvido — mesmo motivo de
        BillingRepository.search: evita o frontend fazer N+1 pra buscar
        o nome de cada paciente da lista.

        Mais recente primeiro (`scheduled_at.desc()`) — mesmo critério
        de ordenação de `list_by_patient` e das demais listas paginadas
        do projeto (ex.: BillingRepository.list_high_risk_paginated).
        """
        base = select(Appointment).where(Appointment.scheduled_at >= date_from, Appointment.scheduled_at <= date_to)
        total = (await self.session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

        stmt = (
            select(Appointment, Patient.full_name)
            .join(Patient, Patient.id == Appointment.patient_id)
            .where(Appointment.scheduled_at >= date_from, Appointment.scheduled_at <= date_to)
            .order_by(Appointment.scheduled_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.all()), total

    async def add(self, appointment: Appointment) -> Appointment:
        self.session.add(appointment)
        await self.session.flush()
        return appointment

    async def save(self, appointment: Appointment) -> Appointment:
        await self.session.flush()
        return appointment
