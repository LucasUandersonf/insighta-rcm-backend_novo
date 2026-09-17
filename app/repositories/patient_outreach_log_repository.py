import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient_outreach_log import PatientOutreachLog


class PatientOutreachLogRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, entry: PatientOutreachLog) -> PatientOutreachLog:
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def list_for_patient(self, patient_id: uuid.UUID, *, limit: int = 50) -> list[PatientOutreachLog]:
        stmt = (
            select(PatientOutreachLog)
            .where(PatientOutreachLog.patient_id == patient_id)
            .order_by(PatientOutreachLog.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def latest_by_patient_ids(self, patient_ids: list[uuid.UUID]) -> dict[uuid.UUID, PatientOutreachLog]:
        """Último contato registrado por paciente, pra anotar as listas
        de reativação (InactivePatients, RfmResponse.action_items) com
        "já contatado em X, resultado Y" — fecha o ciclo que antes só
        apontava quem contatar, sem saber quem já foi. Batch por lista
        de ids explícita (mesmo padrão de PatientRepository.vip_signals_for),
        nunca a tabela inteira."""
        if not patient_ids:
            return {}

        stmt = (
            select(PatientOutreachLog)
            .where(PatientOutreachLog.patient_id.in_(patient_ids))
            .order_by(PatientOutreachLog.patient_id, PatientOutreachLog.created_at.desc())
        )
        result = await self.session.execute(stmt)
        latest: dict[uuid.UUID, PatientOutreachLog] = {}
        for entry in result.scalars().all():
            if entry.patient_id not in latest:
                latest[entry.patient_id] = entry
        return latest
