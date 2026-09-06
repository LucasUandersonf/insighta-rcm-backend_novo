import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.models.patient import Patient
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.patient_repository import PatientRepository
from app.schemas.patient import PatientCreateRequest, PatientResponse

# Placeholder usado por anonymize_patient() — nunca um nome real, nunca
# vazio (um `full_name` vazio quebraria qualquer tela que assume o campo
# não-nulo/não-vazio para exibição).
_ANONYMIZED_NAME_PLACEHOLDER = "[Paciente anonimizado]"


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

    async def anonymize_patient(self, tenant_id: str, actor_user_id: uuid.UUID | None, patient_id: uuid.UUID) -> PatientResponse:
        """
        Direito de eliminação do titular (LGPD art. 18, VI) — ver DECISÃO
        completa em app/sql/022_patient_lgpd_erasure.sql sobre por que
        isto é uma ANONIMIZAÇÃO, nunca um DELETE físico: apagar a linha
        quebraria a integridade referencial com appointments/billing
        (histórico que a clínica é OBRIGADA a reter por obrigação legal
        — a própria LGPD, art. 16, permite isso). `full_name` vira um
        placeholder (nunca fica vazio/nulo — telas que exibem o nome não
        esperam isso); `cpf`/`birth_date`/`acquisition_*` viram NULL.
        """
        patient = await self.repo.get_by_id(patient_id)
        if patient is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paciente não encontrado neste tenant.")
        if patient.anonymized_at is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Este paciente já foi anonimizado.")

        patient.full_name = _ANONYMIZED_NAME_PLACEHOLDER
        patient.cpf = None
        patient.birth_date = None
        patient.acquisition_source = None
        patient.acquisition_campaign_id = None
        patient.anonymized_at = datetime.now(timezone.utc)
        await self.repo.save(patient)

        # Sem diff — o próprio "antes" é o dado pessoal que está sendo
        # eliminado; gravá-lo no audit log derrotaria o propósito do
        # pedido (ver DECISÃO em AuditLogRepository.record).
        await self.audit_repo.record(
            tenant_id=uuid.UUID(tenant_id),
            actor_user_id=actor_user_id,
            action="anonymized",
            entity_type="patient",
            entity_id=patient.id,
        )
        return PatientResponse.model_validate(patient)

    async def list_patients(self, limit: int = 50, offset: int = 0) -> list[PatientResponse]:
        items = await self.repo.list_all(limit=limit, offset=offset)
        return [PatientResponse.model_validate(i) for i in items]

    async def list_patients_paginated(self, limit: int = 50, offset: int = 0) -> tuple[list[PatientResponse], int]:
        items = await self.repo.list_all(limit=limit, offset=offset)
        total = await self.repo.count_all()
        return [PatientResponse.model_validate(i) for i in items], total
