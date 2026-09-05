import uuid

from app.models.patient import Patient
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.patient_repository import PatientRepository
from app.schemas.patient import PatientCreateRequest, PatientResponse


class PatientService:
    def __init__(self, repo: PatientRepository, audit_repo: AuditLogRepository):
        self.repo = repo
        self.audit_repo = audit_repo

    async def create_patient(self, tenant_id: str, actor_user_id: uuid.UUID | None, data: PatientCreateRequest) -> PatientResponse:
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
        # DECISÃO — sem `diff`: ver DECISÃO completa em
        # AuditLogRepository.record. Cadastro de paciente é dado
        # ALTAMENTE sensível (nome, CPF) — o audit log prova QUE um
        # paciente foi criado, POR QUEM, QUANDO, nunca duplica o próprio
        # dado pessoal num segundo lugar.
        await self.audit_repo.record(
            tenant_id=uuid.UUID(tenant_id),
            actor_user_id=actor_user_id,
            action="created",
            entity_type="patient",
            entity_id=saved.id,
        )
        return PatientResponse.model_validate(saved)

    async def list_patients(self, limit: int = 50, offset: int = 0) -> list[PatientResponse]:
        items = await self.repo.list_all(limit=limit, offset=offset)
        return [PatientResponse.model_validate(i) for i in items]

    async def list_patients_paginated(self, limit: int = 50, offset: int = 0) -> tuple[list[PatientResponse], int]:
        items = await self.repo.list_all(limit=limit, offset=offset)
        total = await self.repo.count_all()
        return [PatientResponse.model_validate(i) for i in items], total
