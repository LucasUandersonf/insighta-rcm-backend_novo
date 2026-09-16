"""
app/services/patient_outreach_log_service.py

Onda 4 do Plano de Ação, item 12 — ver DECISÃO completa em
app/sql/055_patient_outreach_log.sql.
"""
import uuid

from fastapi import HTTPException, status

from app.models.patient_outreach_log import PatientOutreachLog
from app.repositories.patient_outreach_log_repository import PatientOutreachLogRepository
from app.repositories.patient_repository import PatientRepository
from app.schemas.patient_outreach_log import PatientOutreachLogCreateRequest, PatientOutreachLogResponse


class PatientOutreachLogService:
    def __init__(self, repo: PatientOutreachLogRepository, patient_repo: PatientRepository):
        self.repo = repo
        self.patient_repo = patient_repo

    async def create_outreach_log(
        self, tenant_id: str, created_by: uuid.UUID, patient_id: uuid.UUID, data: PatientOutreachLogCreateRequest
    ) -> PatientOutreachLogResponse:
        patient = await self.patient_repo.get_by_id(patient_id)
        if patient is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paciente não encontrado neste tenant.")

        entry = PatientOutreachLog(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            patient_id=patient_id,
            channel=data.channel,
            outcome=data.outcome,
            notes=data.notes,
            created_by=created_by,
        )
        saved = await self.repo.add(entry)
        return PatientOutreachLogResponse.model_validate(saved)

    async def list_outreach_log(self, patient_id: uuid.UUID) -> list[PatientOutreachLogResponse]:
        entries = await self.repo.list_for_patient(patient_id)
        return [PatientOutreachLogResponse.model_validate(entry) for entry in entries]
