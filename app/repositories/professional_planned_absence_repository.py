import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.professional_planned_absence import ProfessionalPlannedAbsence


class ProfessionalPlannedAbsenceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_by_professional(self, professional_id: uuid.UUID) -> list[ProfessionalPlannedAbsence]:
        stmt = (
            select(ProfessionalPlannedAbsence)
            .where(ProfessionalPlannedAbsence.professional_id == professional_id)
            .order_by(ProfessionalPlannedAbsence.start_date)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_professionals(
        self, professional_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[ProfessionalPlannedAbsence]]:
        """Versão em lote — mesmo raciocínio de
        ProfessionalAvailabilityRepository.list_by_professionals: uma
        query com `IN (...)` em vez de uma por profissional dentro de um
        loop em ProfessionalService.list_professionals."""
        if not professional_ids:
            return {}
        stmt = (
            select(ProfessionalPlannedAbsence)
            .where(ProfessionalPlannedAbsence.professional_id.in_(professional_ids))
            .order_by(ProfessionalPlannedAbsence.start_date)
        )
        result = await self.session.execute(stmt)
        grouped: dict[uuid.UUID, list[ProfessionalPlannedAbsence]] = {pid: [] for pid in professional_ids}
        for absence in result.scalars().all():
            grouped[absence.professional_id].append(absence)
        return grouped

    async def get_by_id(self, absence_id: uuid.UUID) -> ProfessionalPlannedAbsence | None:
        stmt = select(ProfessionalPlannedAbsence).where(ProfessionalPlannedAbsence.id == absence_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def add(self, absence: ProfessionalPlannedAbsence) -> ProfessionalPlannedAbsence:
        self.session.add(absence)
        await self.session.flush()
        return absence

    async def delete(self, absence: ProfessionalPlannedAbsence) -> None:
        await self.session.delete(absence)
        await self.session.flush()
