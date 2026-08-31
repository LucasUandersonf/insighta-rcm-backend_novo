import uuid

from app.models.patient import Patient
from app.repositories.patient_repository import PatientRepository
from app.schemas.patient import PatientCreateRequest, PatientResponse


class PatientService:
    def __init__(self, repo: PatientRepository):
        self.repo = repo

    async def create_patient(self, tenant_id: str, data: PatientCreateRequest) -> PatientResponse:
        patient = Patient(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            full_name=data.full_name,
            cpf=data.cpf,
            birth_date=data.birth_date,
            acquisition_source=data.acquisition_source,
            acquisition_campaign_id=data.acquisition_campaign_id,
        )
        saved = await self.repo.add(patient)
        return PatientResponse.model_validate(saved)

    async def list_patients(self, limit: int = 50, offset: int = 0) -> list[PatientResponse]:
        items = await self.repo.list_all(limit=limit, offset=offset)
        return [PatientResponse.model_validate(i) for i in items]

    async def list_patients_paginated(self, limit: int = 50, offset: int = 0) -> tuple[list[PatientResponse], int]:
        items = await self.repo.list_all(limit=limit, offset=offset)
        total = await self.repo.count_all()
        return [PatientResponse.model_validate(i) for i in items], total
