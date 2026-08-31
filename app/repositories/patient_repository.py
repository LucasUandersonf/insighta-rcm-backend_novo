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
