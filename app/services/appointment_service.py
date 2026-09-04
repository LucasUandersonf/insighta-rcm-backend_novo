import uuid

from fastapi import HTTPException, status

from app.models.appointment import Appointment
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.patient_repository import PatientRepository
from app.repositories.professional_repository import ProfessionalRepository
from app.schemas.appointment import AppointmentCreateRequest, AppointmentResponse, AppointmentUpdateRequest
from app.services.no_show_risk_engine import assess as assess_no_show_risk


class AppointmentService:
    def __init__(
        self,
        appointment_repo: AppointmentRepository,
        patient_repo: PatientRepository,
        professional_repo: ProfessionalRepository,
    ):
        self.appointment_repo = appointment_repo
        self.patient_repo = patient_repo
        self.professional_repo = professional_repo

    async def create_appointment(self, tenant_id: str, created_by: str, data: AppointmentCreateRequest) -> AppointmentResponse:
        # Validação de integridade de negócio (além do FK do banco): o
        # paciente precisa existir E pertencer ao MESMO tenant. Repare que
        # nem precisamos comparar tenant_id explicitamente aqui — se o
        # patient_id pertencer a outro tenant, get_by_id() simplesmente
        # não o encontra (RLS já filtrou), então a checagem abaixo já
        # cobre isso "de graça".
        patient = await self.patient_repo.get_by_id(data.patient_id)
        if patient is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Paciente não encontrado neste tenant.",
            )

        if data.professional_id is not None:
            professional = await self.professional_repo.get_by_id(data.professional_id)
            if professional is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Profissional não encontrado neste tenant.",
                )

        appointment = Appointment(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            patient_id=data.patient_id,
            insurance_plan_id=data.insurance_plan_id,
            professional_id=data.professional_id,
            scheduled_at=data.scheduled_at,
            duration_minutes=data.duration_minutes,
            status="scheduled",
            procedure_code=data.procedure_code,
            cid_code=data.cid_code,
            created_by=uuid.UUID(created_by),
        )

        # Motor de risco de falta (Fase 1): olha só para o histórico
        # PASSADO deste paciente, anterior ao horário deste novo
        # agendamento — nunca usa dado futuro nem o próprio registro
        # sendo criado.
        history = await self.appointment_repo.list_past_by_patient(data.patient_id, before=data.scheduled_at)
        risk = assess_no_show_risk(history, data.scheduled_at)
        appointment.no_show_risk_level = risk.risk_level
        appointment.no_show_risk_score = risk.score

        saved = await self.appointment_repo.add(appointment)
        return AppointmentResponse.model_validate(saved)

    async def list_by_patient(self, patient_id: uuid.UUID) -> list[AppointmentResponse]:
        items = await self.appointment_repo.list_by_patient(patient_id)
        return [AppointmentResponse.model_validate(i) for i in items]

    async def update_appointment(self, appointment_id: uuid.UUID, data: AppointmentUpdateRequest) -> AppointmentResponse:
        """
        Fecha o ciclo Agendamento -> Atendimento que faltava (ver DECISÃO
        em AppointmentUpdateRequest): a recepção marca falta/cancelamento,
        ou o profissional confirma o atendimento e só agora informa
        procedimento/CID, sem precisar ter adivinhado isso na hora de
        marcar o horário.
        """
        appointment = await self.appointment_repo.get_by_id(appointment_id)
        if appointment is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agendamento não encontrado neste tenant.")

        if data.status is not None:
            appointment.status = data.status
        if data.procedure_code is not None:
            appointment.procedure_code = data.procedure_code
        if data.cid_code is not None:
            appointment.cid_code = data.cid_code
        if data.duration_minutes is not None:
            appointment.duration_minutes = data.duration_minutes

        await self.appointment_repo.save(appointment)
        return AppointmentResponse.model_validate(appointment)
