"""Mesmo padrão de billing_repository.py: sem WHERE tenant_id manual — o
RLS, sob a sessão tenant-aware injetada pelo endpoint, já garante isso."""
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient


class PatientRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self, limit: int = 50, offset: int = 0) -> list[Patient]:
        stmt = select(Patient).order_by(Patient.full_name).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_all(self) -> int:
        stmt = select(func.count()).select_from(Patient)
        return (await self.session.execute(stmt)).scalar_one()

    async def get_by_id(self, patient_id: uuid.UUID) -> Patient | None:
        stmt = select(Patient).where(Patient.id == patient_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_cpf(self, cpf: str) -> Patient | None:
        stmt = select(Patient).where(Patient.cpf == cpf)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def add(self, patient: Patient) -> Patient:
        self.session.add(patient)
        await self.session.flush()
        return patient

    async def save(self, patient: Patient) -> Patient:
        await self.session.flush()
        return patient

    async def vip_signals_for(self, patient_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[int, int]]:
        """(visit_count, referral_count) por paciente — insumo de
        patient_value_engine.compute_vip_status ("Equilíbrio Insighta",
        perna Cliente). `visit_count` conta atendimentos NÃO cancelados
        (mesmo critério de `_EARLY_CHURN_CTE` em analytics_repository.py);
        `referral_count` conta quantos OUTROS pacientes têm este como
        `referred_by_patient_id`.

        Batch por lista de ids explícita (não a base inteira do tenant)
        — a tela de pacientes já pagina (ver PatientService.
        list_patients_paginated), então só precisa dos sinais da PÁGINA
        atual, nunca de todo mundo de uma vez."""
        if not patient_ids:
            return {}

        from app.models.appointment import Appointment

        visit_stmt = (
            select(Appointment.patient_id, func.count())
            .where(Appointment.patient_id.in_(patient_ids), Appointment.status != "cancelled")
            .group_by(Appointment.patient_id)
        )
        visit_counts = {row[0]: row[1] for row in (await self.session.execute(visit_stmt)).all()}

        referral_stmt = (
            select(Patient.referred_by_patient_id, func.count())
            .where(Patient.referred_by_patient_id.in_(patient_ids))
            .group_by(Patient.referred_by_patient_id)
        )
        referral_counts = {row[0]: row[1] for row in (await self.session.execute(referral_stmt)).all()}

        return {pid: (visit_counts.get(pid, 0), referral_counts.get(pid, 0)) for pid in patient_ids}
