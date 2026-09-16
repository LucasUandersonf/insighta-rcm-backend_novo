import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import HTTPException, status

from app.core.config import get_settings
from app.core.security import generate_satisfaction_token
from app.models.appointment import Appointment
from app.models.appointment_satisfaction_token import AppointmentSatisfactionToken
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.appointment_satisfaction_token_repository import AppointmentSatisfactionTokenRepository
from app.repositories.local_repository import LocalRepository
from app.repositories.patient_repository import PatientRepository
from app.repositories.professional_repository import ProfessionalRepository
from app.repositories.tenant_repository import TenantRepository
from app.repositories.webhook_subscription_repository import WebhookSubscriptionRepository
from app.schemas.appointment import AppointmentCreateRequest, AppointmentListItem, AppointmentResponse, AppointmentUpdateRequest
from app.schemas.appointment_satisfaction import SatisfactionLinkResponse
from app.schemas.pagination import PaginatedResponse
from app.services.no_show_risk_engine import assess as assess_no_show_risk
from app.services.no_show_risk_engine import resolve_thresholds
from app.services.webhook_dispatch_service import dispatch_event

settings = get_settings()


class AppointmentService:
    def __init__(
        self,
        appointment_repo: AppointmentRepository,
        patient_repo: PatientRepository,
        professional_repo: ProfessionalRepository,
        local_repo: LocalRepository,
        tenant_repo: TenantRepository,
        satisfaction_token_repo: AppointmentSatisfactionTokenRepository,
        webhook_repo: WebhookSubscriptionRepository | None = None,
    ):
        self.appointment_repo = appointment_repo
        self.patient_repo = patient_repo
        self.professional_repo = professional_repo
        self.local_repo = local_repo
        self.tenant_repo = tenant_repo
        self.satisfaction_token_repo = satisfaction_token_repo
        # Opcional (default None) — mesmo critério de BillingService.webhook_repo:
        # recurso opt-in do tenant, não obrigatório como audit_repo em
        # outros services. Só usado aqui via POST /appointments (criação
        # manual, um registro por vez) — DE PROPÓSITO não ligado ao path
        # de ingestão em lote da Agenda (normalization_service.py): um
        # arquivo com centenas de linhas geraria dezenas de chamadas HTTP
        # síncronas durante o upload, arriscando travar um caminho crítico
        # já bem testado. Ver DECISÃO completa em webhook_dispatch_service.py.
        self.webhook_repo = webhook_repo

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

        if data.local_id is not None:
            local = await self.local_repo.get_by_id(data.local_id)
            if local is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Local de atendimento não encontrado neste tenant.",
                )

        appointment = Appointment(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            patient_id=data.patient_id,
            insurance_plan_id=data.insurance_plan_id,
            professional_id=data.professional_id,
            local_id=data.local_id,
            tipo_paciente=data.tipo_paciente,
            scheduled_at=data.scheduled_at,
            duration_minutes=data.duration_minutes,
            status="scheduled",
            procedure_code=data.procedure_code,
            cid_code=data.cid_code,
            visit_intent_tag=data.visit_intent_tag,
            created_by=uuid.UUID(created_by),
        )

        # Motor de risco de falta (Fase 1): olha só para o histórico
        # PASSADO deste paciente, anterior ao horário deste novo
        # agendamento — nunca usa dado futuro nem o próprio registro
        # sendo criado. Limiares vêm de Tenant (None = default do
        # módulo) — ver DECISÃO em no_show_risk_engine.resolve_thresholds.
        history = await self.appointment_repo.list_past_by_patient(data.patient_id, before=data.scheduled_at)
        tenant = await self.tenant_repo.get_by_id(uuid.UUID(tenant_id))
        low_threshold, medium_threshold = resolve_thresholds(tenant)
        # Raio-X da Receita, frente "Prevendo movimentos": antecedência
        # da marcação como segundo sinal de risco de falta (ver DECISÃO
        # completa em no_show_risk_engine.assess). Calculado AQUI (não
        # dentro do motor, que continua puro/sem ler o relógio) — max(...,
        # 0) porque `scheduled_at` sempre deveria ser futuro nesta rota,
        # mas nunca reporta antecedência negativa por um possível
        # arredondamento de milissegundos entre a validação e este ponto.
        candidate_lead_time_days = max((data.scheduled_at - datetime.now(timezone.utc)).days, 0)
        risk = assess_no_show_risk(
            history,
            data.scheduled_at,
            low_threshold=low_threshold,
            medium_threshold=medium_threshold,
            candidate_lead_time_days=candidate_lead_time_days,
        )
        appointment.no_show_risk_level = risk.risk_level
        appointment.no_show_risk_score = risk.score

        saved = await self.appointment_repo.add(appointment)

        # Terceiro evento ligado ao motor de webhooks (ver DECISÃO em
        # webhook_dispatch_service.py) — só dispara no nível "alto", não
        # em todo agendamento criado: o cliente quer ser avisado do que
        # precisa de ação (ligar para confirmar), não de cada consulta
        # marcada. Nunca carrega nome de paciente/CID — só ids e o
        # próprio nível/score de risco, já calculados acima.
        if risk.risk_level == "alto" and self.webhook_repo is not None:
            await dispatch_event(
                self.webhook_repo,
                event_type="no_show_risk.high",
                payload={
                    "appointment_id": saved.id,
                    "patient_id": saved.patient_id,
                    "scheduled_at": saved.scheduled_at,
                    "no_show_risk_score": risk.score,
                },
            )

        return AppointmentResponse.model_validate(saved)

    async def list_by_patient(self, patient_id: uuid.UUID) -> list[AppointmentResponse]:
        items = await self.appointment_repo.list_by_patient(patient_id)
        return [AppointmentResponse.model_validate(i) for i in items]

    async def list_by_date_range_paginated(
        self, date_from: date, date_to: date, *, limit: int, offset: int
    ) -> PaginatedResponse[AppointmentListItem]:
        """
        Peça que faltava depois do Achado 12 da Auditoria de Templates e
        Insights: os insights de canal de agendamento/motivo de
        cancelamento apontavam o problema em agregado, mas não existia
        nenhuma tela de listagem de agendamentos individuais pra mostrar
        QUAL agendamento tem qual canal/motivo. Mesmo padrão de
        _bounds/paginação já usado no resto do produto (ver
        AnalyticsRepository._bounds, BillingService.list_high_risk_paginated).
        """
        start = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
        end = datetime.combine(date_to, time.max, tzinfo=timezone.utc)
        rows, total = await self.appointment_repo.list_by_date_range_paginated(start, end, limit=limit, offset=offset)
        items = [
            AppointmentListItem(
                id=appointment.id,
                patient_name=patient_name,
                scheduled_at=appointment.scheduled_at,
                status=appointment.status,
                procedure_code=appointment.procedure_code,
                visit_type=appointment.visit_type,
                booking_channel=appointment.booking_channel,
                cancellation_reason=appointment.cancellation_reason,
                visit_intent_tag=appointment.visit_intent_tag,
            )
            for appointment, patient_name in rows
        ]
        return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)

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
        if data.local_id is not None:
            local = await self.local_repo.get_by_id(data.local_id)
            if local is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Local de atendimento não encontrado neste tenant.")
            appointment.local_id = data.local_id
        if data.tipo_paciente is not None:
            appointment.tipo_paciente = data.tipo_paciente
        if data.visit_intent_tag is not None:
            appointment.visit_intent_tag = data.visit_intent_tag
        if data.addon_offered_procedure is not None:
            appointment.addon_offered_procedure = data.addon_offered_procedure
        if data.addon_declined is not None:
            # "Mapa de Dados Insighta" — nunca grava um resultado de oferta
            # sem o que foi oferecido (nem nesta chamada, nem em uma
            # anterior) — ver DECISÃO em 050_appointment_addon_upsell.sql.
            if appointment.addon_offered_procedure is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="addon_declined requer addon_offered_procedure preenchido.",
                )
            appointment.addon_declined = data.addon_declined

        await self.appointment_repo.save(appointment)
        return AppointmentResponse.model_validate(appointment)

    async def generate_satisfaction_link(self, appointment_id: uuid.UUID) -> SatisfactionLinkResponse:
        """"Mapa de Dados Insighta" — Domínio Pós-atendimento (Onda 2),
        pilar Satisfação/NPS: gera um link público de uso único (ver
        DECISÃO completa em 052_appointment_satisfaction.sql sobre por
        que não é uma mensagem automática de WhatsApp) — a recepção
        copia `url` e envia manualmente ao paciente pelo canal que já
        usa. Só faz sentido para um atendimento já REALIZADO: pedir
        satisfação de uma consulta que ainda vai acontecer (ou que o
        paciente faltou) não tem o que avaliar."""
        appointment = await self.appointment_repo.get_by_id(appointment_id)
        if appointment is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agendamento não encontrado neste tenant.")
        if appointment.status != "completed":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Só é possível gerar link de avaliação para um atendimento já realizado (status 'completed').",
            )

        raw_token, token_hash = generate_satisfaction_token()
        expires_at = datetime.now(timezone.utc) + timedelta(days=settings.SATISFACTION_TOKEN_EXPIRE_DAYS)
        await self.satisfaction_token_repo.add(
            AppointmentSatisfactionToken(
                id=uuid.uuid4(),
                appointment_id=appointment.id,
                tenant_id=appointment.tenant_id,
                token_hash=token_hash,
                expires_at=expires_at,
            )
        )
        return SatisfactionLinkResponse(url=f"{settings.FRONTEND_BASE_URL}/satisfacao/{raw_token}", expires_at=expires_at)
