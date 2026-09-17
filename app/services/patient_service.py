import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.models.patient import Patient
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.patient_repository import PatientRepository
from app.schemas.patient import (
    PatientBirthdayItem,
    PatientBirthdaysResponse,
    PatientCreateRequest,
    PatientResponse,
    PatientUpdateRequest,
)
from app.services.patient_value_engine import compute_vip_status

# Placeholder usado por anonymize_patient() — nunca um nome real, nunca
# vazio (um `full_name` vazio quebraria qualquer tela que assume o campo
# não-nulo/não-vazio para exibição).
_ANONYMIZED_NAME_PLACEHOLDER = "[Paciente anonimizado]"


class PatientService:
    def __init__(self, repo: PatientRepository, audit_repo: AuditLogRepository):
        self.repo = repo
        self.audit_repo = audit_repo

    async def create_patient(self, tenant_id: str, actor_user_id: uuid.UUID | None, data: PatientCreateRequest) -> PatientResponse:
        if data.referred_by_patient_id is not None:
            # Ver DECISÃO em 045_patient_relationship_fields.sql: a FK
            # sozinha não impede referenciar um paciente de OUTRO
            # tenant, então a validação real é aqui — busca pelo MESMO
            # repositório com RLS ativo (só enxerga o tenant atual). Não
            # encontrado = ou não existe, ou é de outro tenant; nos dois
            # casos, 422.
            referrer = await self.repo.get_by_id(data.referred_by_patient_id)
            if referrer is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Paciente indicador não encontrado neste tenant.",
                )
        patient = Patient(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            full_name=data.full_name,
            cpf=data.cpf,
            birth_date=data.birth_date,
            acquisition_source=data.acquisition_source,
            acquisition_campaign_id=data.acquisition_campaign_id,
            referred_by_patient_id=data.referred_by_patient_id,
            communication_consent=data.communication_consent,
            preferred_time_window=data.preferred_time_window,
            zip_code=data.zip_code,
            phone=data.phone,
            email=data.email,
            sex=data.sex,
            address_street=data.address_street,
            address_city=data.address_city,
            address_state=data.address_state,
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
        # "Mapa de Dados Insighta" — zip_code é dado pessoal de
        # localização, entra na mesma eliminação. communication_consent/
        # preferred_time_window/referred_by_patient_id NÃO identificam o
        # titular sozinhos e referred_by_patient_id ainda sustenta o
        # histórico de indicação de OUTROS pacientes — preservados.
        patient.zip_code = None
        # Escopo completo de pessoa física (pedido do usuário) — telefone/
        # e-mail/endereço/sexo são dado pessoal direto, mesma eliminação
        # de cpf/birth_date/zip_code acima (ver DECISÃO em
        # app/sql/058_patient_full_identity.sql).
        patient.phone = None
        patient.email = None
        patient.sex = None
        patient.address_street = None
        patient.address_city = None
        patient.address_state = None
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

    async def update_patient(self, patient_id: uuid.UUID, data: PatientUpdateRequest) -> PatientResponse:
        """
        "Mapa de Dados Insighta" — Domínio Paciente (Onda 1): estes 4
        campos raramente são conhecidos no primeiro cadastro (quem
        indicou, consentimento de contato, preferência de horário, CEP)
        — este endpoint deixa completá-los depois, sem reabrir o
        cadastro inteiro. Mesmo contrato parcial de
        ProfessionalService.update_professional: só `is not None` é
        aplicado, nunca limpa um campo já preenchido de volta pra NULL.
        """
        patient = await self.repo.get_by_id(patient_id)
        if patient is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paciente não encontrado neste tenant.")

        if data.referred_by_patient_id is not None:
            if data.referred_by_patient_id == patient.id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Um paciente não pode ser indicado por si mesmo."
                )
            referrer = await self.repo.get_by_id(data.referred_by_patient_id)
            if referrer is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Paciente indicador não encontrado neste tenant.",
                )
            patient.referred_by_patient_id = data.referred_by_patient_id
        if data.communication_consent is not None:
            patient.communication_consent = data.communication_consent
        if data.preferred_time_window is not None:
            patient.preferred_time_window = data.preferred_time_window
        if data.zip_code is not None:
            patient.zip_code = data.zip_code
        # Escopo completo de pessoa física (pedido do usuário) — mesmo
        # contrato parcial de sempre (só `is not None` é aplicado).
        if data.birth_date is not None:
            patient.birth_date = data.birth_date
        if data.phone is not None:
            patient.phone = data.phone
        if data.email is not None:
            patient.email = data.email
        if data.sex is not None:
            patient.sex = data.sex
        if data.address_street is not None:
            patient.address_street = data.address_street
        if data.address_city is not None:
            patient.address_city = data.address_city
        if data.address_state is not None:
            patient.address_state = data.address_state
        await self.repo.save(patient)
        return PatientResponse.model_validate(patient)

    async def list_patients(self, limit: int = 50, offset: int = 0) -> list[PatientResponse]:
        items = await self.repo.list_all(limit=limit, offset=offset)
        return [PatientResponse.model_validate(i) for i in items]

    async def list_patients_paginated(self, limit: int = 50, offset: int = 0) -> tuple[list[PatientResponse], int]:
        """
        "Equilíbrio Insighta" (Balanced Scorecard, perna Cliente,
        mecanismo 1): cada paciente já sai desta listagem com o sinal de
        alto valor ("VIP") calculado — é esta MESMA listagem que alimenta
        o seletor de paciente na tela de Agenda (ver AppointmentsPage.tsx,
        frontend), então a recepção já vê o selo VIP no momento exato de
        marcar a consulta, sem precisar de uma tela separada.
        """
        items = await self.repo.list_all(limit=limit, offset=offset)
        total = await self.repo.count_all()
        vip_signals = await self.repo.vip_signals_for([i.id for i in items])
        responses = []
        for item in items:
            visit_count, referral_count = vip_signals.get(item.id, (0, 0))
            vip = compute_vip_status(visit_count=visit_count, referral_count=referral_count)
            response = PatientResponse.model_validate(item).model_copy(
                update={"is_vip": vip.is_vip, "vip_reasons": vip.reasons}
            )
            responses.append(response)
        return responses, total

    async def list_birthdays_in_month(self, month: int) -> PatientBirthdaysResponse:
        """
        Achado do Dossiê Insighta RCM — lista de aniversariantes do mês
        (ação clássica de relacionamento/retenção). `birth_date` nunca é
        None aqui: o repositório já filtra por isso na consulta.
        """
        patients = await self.repo.list_birthdays_in_month(month)
        items = [
            PatientBirthdayItem(
                patient_id=p.id,
                full_name=p.full_name,
                birth_date=p.birth_date,
                communication_consent=p.communication_consent,
            )
            for p in patients
        ]
        return PatientBirthdaysResponse(month=month, items=items)
