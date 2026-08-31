import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.professional_availability import ProfessionalAvailability


class ProfessionalAvailabilityRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_by_professional(self, professional_id: uuid.UUID) -> list[ProfessionalAvailability]:
        stmt = select(ProfessionalAvailability).where(ProfessionalAvailability.professional_id == professional_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_professionals(self, professional_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[ProfessionalAvailability]]:
        """
        Versão em lote de list_by_professional — corrige um N+1 real que
        existia em ProfessionalService.list_professionals (uma query de
        disponibilidade POR profissional, dentro de um loop). Com poucos
        profissionais por tenant isso não doía, mas é o tipo de padrão
        que degrada de forma óbvia à medida que uma clínica cresce.
        Uma query com `IN (...)`, agrupada em Python depois, substitui N
        queries por 1.
        """
        if not professional_ids:
            return {}
        stmt = select(ProfessionalAvailability).where(ProfessionalAvailability.professional_id.in_(professional_ids))
        result = await self.session.execute(stmt)
        grouped: dict[uuid.UUID, list[ProfessionalAvailability]] = {pid: [] for pid in professional_ids}
        for block in result.scalars().all():
            grouped[block.professional_id].append(block)
        return grouped

    async def add(self, block: ProfessionalAvailability) -> ProfessionalAvailability:
        self.session.add(block)
        await self.session.flush()
        return block
