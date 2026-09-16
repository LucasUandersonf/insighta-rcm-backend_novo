"""
app/services/billing_service.py

create_billing() chama o motor de regras (denial_risk_engine.assess),
usando o appointment e o ContractItem vigente (tabela de preços
homologada — ver ContractItemRepository.find_agreed_price) para decidir
status e risco na hora da COBRANÇA.

settle_billing() é o outro lado do cruzamento, que só existe depois que
o lote é liquidado pela operadora: registra received_value (quanto foi
REALMENTE repassado) — é isso que alimenta a Divergência de Recebimento
(underpayment) no dashboard, separada da Divergência de Cobrança que
denial_risk_engine já cobre.
"""
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.models.billing import Billing
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.billing_repository import BillingRepository
from app.repositories.contract_item_repository import ContractItemRepository
from app.repositories.guia_repository import GuiaRepository
from app.repositories.webhook_subscription_repository import WebhookSubscriptionRepository
from app.schemas.billing import (
    BillingClinicalDocumentationConfirmationRequest,
    BillingCoparticipationConfirmationRequest,
    BillingCreateRequest,
    BillingResponse,
    BillingSearchItem,
    BillingSettleRequest,
)
from app.schemas.pagination import PaginatedResponse
from app.services.denial_risk_engine import assess
from app.services.webhook_dispatch_service import dispatch_event


class BillingService:
    def __init__(
        self,
        billing_repo: BillingRepository,
        appointment_repo: AppointmentRepository,
        contract_item_repo: ContractItemRepository,
        guia_repo: GuiaRepository | None = None,
        *,
        audit_repo: AuditLogRepository,
        webhook_repo: WebhookSubscriptionRepository | None = None,
    ):
        self.billing_repo = billing_repo
        self.appointment_repo = appointment_repo
        self.contract_item_repo = contract_item_repo
        # Opcional por ora (default None) para não quebrar quem já
        # instancia BillingService sem essa dependência (ver
        # app/api/v1/endpoints/billing.py) — só é de fato usado quando
        # guia_id vem preenchido no payload.
        self.guia_repo = guia_repo
        # DIFERENTE de guia_repo acima: obrigatório, não opcional. O bug
        # que esta rodada corrige é EXATAMENTE "auditoria que falha
        # calada" (a tabela existia, nada gravava nela, nenhum teste
        # denunciava) — deixar este parâmetro opcional reintroduziria o
        # mesmo risco em qualquer chamador futuro que esquecesse de
        # passá-lo.
        self.audit_repo = audit_repo
        # Opcional (default None), diferente de audit_repo: disparo de
        # webhook é um RECURSO OPT-IN do tenant (só existe efeito se ele
        # tiver cadastrado alguma assinatura em Integrações), não uma
        # obrigação legal como a trilha de auditoria — ver DECISÃO em
        # webhook_dispatch_service.py.
        self.webhook_repo = webhook_repo

    async def create_billing(
        self, tenant_id: str, actor_user_id: uuid.UUID | None, data: BillingCreateRequest
    ) -> BillingResponse:
        # Mesma observação de sempre: se appointment_id pertencer a outro
        # tenant, o RLS já o esconde daqui — "não encontrado" cobre os
        # dois casos (não existe / não é deste tenant) sem checagem extra.
        appointment = await self.appointment_repo.get_by_id(data.appointment_id)
        if appointment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Atendimento não encontrado neste tenant.",
            )

        if data.guia_id is not None:
            if self.guia_repo is None:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Serviço de faturamento não configurado para validar guia.",
                )
            guia = await self.guia_repo.get_by_id(data.guia_id)
            if guia is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Guia não encontrada neste tenant.")

        contract_item = None
        if appointment.procedure_code:
            contract_item = await self.contract_item_repo.find_agreed_price(
                insurance_plan_id=data.insurance_plan_id,
                tuss_code=appointment.procedure_code,
            )

        # Raio-X da Receita, frente "Evitando perdas": resolve o sinal de
        # duplicidade AQUI (repositório) — o motor de regras continua
        # puro/sem banco (ver DECISÃO em denial_risk_engine.py e
        # BillingRepository.has_duplicate).
        has_duplicate = await self.billing_repo.has_duplicate(
            data.appointment_id, data.charged_value, data.item_type
        )

        # quantity entra no motor de regras (ver DECISÃO em
        # denial_risk_engine.assess) para multiplicar o preço de tabela —
        # sem isso, lançamento manual de quantidade > 1 sofreria o mesmo
        # falso positivo já corrigido na ingestão em lote.
        risk = assess(
            appointment, contract_item, data.charged_value, quantity=data.quantity, has_duplicate_billing=has_duplicate
        )

        billing = Billing(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            appointment_id=data.appointment_id,
            insurance_plan_id=data.insurance_plan_id,
            charged_value=data.charged_value,
            guia_id=data.guia_id,
            # Regra de negócio do briefing: risco alto barra o envio
            # automaticamente -> status "held_for_review" em vez de "pending".
            status="held_for_review" if risk.should_hold_for_review else "pending",
            denial_risk_level=risk.level,
            denial_reasons=risk.reasons,
            value_saved_by_correction=float(risk.value_saved_by_correction),
            quantity=data.quantity,
            member_card_number=data.member_card_number,
            item_type=data.item_type,
            coparticipation_value=data.coparticipation_value,
            payment_method=data.payment_method,
            installments=data.installments,
        )
        saved = await self.billing_repo.add(billing)
        # Sem `diff` — ver DECISÃO em AuditLogRepository.record. O
        # faturamento em si (valor cobrado, risco de glosa) fica na
        # própria linha de `billing`; o audit log só prova QUE foi criado,
        # POR QUEM, QUANDO.
        await self.audit_repo.record(
            tenant_id=uuid.UUID(tenant_id), actor_user_id=actor_user_id, action="created", entity_type="billing", entity_id=saved.id
        )
        # Primeiro evento real do motor de webhooks OUTBOUND (ver DECISÃO
        # em webhook_dispatch_service.py) — held_for_review é o momento em
        # que o cliente mais quer ser avisado num canal que ele já olha
        # (Slack/CRM), sem precisar abrir o painel para descobrir.
        if risk.should_hold_for_review and self.webhook_repo is not None:
            await dispatch_event(
                self.webhook_repo,
                event_type="billing.held_for_review",
                payload={
                    "billing_id": saved.id,
                    "appointment_id": saved.appointment_id,
                    "denial_risk_level": saved.denial_risk_level,
                    "denial_reasons": saved.denial_reasons,
                    "charged_value": float(saved.charged_value),
                },
            )
        return BillingResponse.model_validate(saved)

    async def list_high_risk(self) -> list[BillingResponse]:
        items = await self.billing_repo.list_high_risk()
        return [BillingResponse.model_validate(i) for i in items]

    async def list_high_risk_paginated(
        self, *, limit: int, offset: int, insurance_plan_id: uuid.UUID | None = None
    ) -> PaginatedResponse[BillingResponse]:
        items, total = await self.billing_repo.list_high_risk_paginated(
            limit=limit, offset=offset, insurance_plan_id=insurance_plan_id
        )
        return PaginatedResponse(
            items=[BillingResponse.model_validate(i) for i in items], total=total, limit=limit, offset=offset
        )

    async def search_billing(self, query: str) -> list[BillingSearchItem]:
        if not query or len(query.strip()) < 2:
            return []  # evita varrer a tabela inteira com 0-1 caractere
        rows = await self.billing_repo.search(query.strip(), limit=20)
        return [
            BillingSearchItem(
                id=billing.id,
                patient_name=patient_name,
                procedure_code=procedure_code,
                insurance_plan_name=plan_name,
                charged_value=float(billing.charged_value),
                status=billing.status,
                denial_risk_level=billing.denial_risk_level,
                created_at=billing.created_at,
                item_type=billing.item_type,
                member_card_number=billing.member_card_number,
                coparticipation_value=float(billing.coparticipation_value) if billing.coparticipation_value is not None else None,
                coparticipation_received=billing.coparticipation_received,
                clinical_documentation_confirmed=billing.clinical_documentation_confirmed,
                payment_method=billing.payment_method,
            )
            for billing, patient_name, procedure_code, plan_name in rows
        ]

    async def settle_billing(
        self, tenant_id: str, actor_user_id: uuid.UUID | None, billing_id: uuid.UUID, data: BillingSettleRequest
    ) -> BillingResponse:
        """
        Liquidação do lote: registra quanto a operadora REALMENTE pagou.
        Não recalcula denial_risk_level (isso é sobre a COBRANÇA, decidido
        na criação) — este passo é só sobre o RECEBIMENTO, um evento
        posterior e independente. A comparação received_value vs.
        contract_items.agreed_price acontece no dashboard
        (analytics_repository.py), não aqui — este método só grava o fato.
        """
        billing = await self.billing_repo.get_by_id(billing_id)
        if billing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Faturamento não encontrado neste tenant.")
        previous_status = billing.status
        billing.received_value = data.received_value
        billing.settled_at = datetime.now(timezone.utc)
        billing.status = "paid"
        await self.billing_repo.save(billing)
        # `diff` só com a transição de STATUS (dado operacional, não
        # financeiro/clínico) — ver DECISÃO em AuditLogRepository.record.
        # `received_value` fica de fora de propósito: é dado financeiro
        # que já vive na própria linha de billing, não precisa de uma
        # segunda cópia aqui.
        await self.audit_repo.record(
            tenant_id=uuid.UUID(tenant_id),
            actor_user_id=actor_user_id,
            action="settled",
            entity_type="billing",
            entity_id=billing.id,
            diff={"status": {"before": previous_status, "after": billing.status}},
        )
        return BillingResponse.model_validate(billing)

    async def confirm_coparticipation(
        self, tenant_id: str, actor_user_id: uuid.UUID | None, billing_id: uuid.UUID, data: BillingCoparticipationConfirmationRequest
    ) -> BillingResponse:
        """
        Épico F4.2 do Plano Diretor ("Fechar lacunas operacionais") —
        confirma (ou não) que a coparticipação cobrada nesta linha foi
        de fato recebida do paciente no momento do atendimento. Ver
        DECISÃO completa em 043_coparticipation_confirmation.sql sobre
        por que o estado inicial é NULL, nunca False.
        """
        billing = await self.billing_repo.get_by_id(billing_id)
        if billing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Faturamento não encontrado neste tenant.")
        if not billing.coparticipation_value:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Este faturamento não tem valor de coparticipação cobrado — nada para confirmar.",
            )
        previous_received = billing.coparticipation_received
        billing.coparticipation_received = data.received
        billing.coparticipation_confirmed_at = datetime.now(timezone.utc)
        billing.coparticipation_confirmed_by = actor_user_id
        # "Mapa de Dados Insighta" — só grava forma de pagamento/parcelas
        # quando de fato foi recebido; `received=False` não tem "como foi
        # pago" pra registrar (nada foi pago).
        if data.received:
            if data.payment_method is not None:
                billing.payment_method = data.payment_method
            if data.installments is not None:
                billing.installments = data.installments
        await self.billing_repo.save(billing)
        await self.audit_repo.record(
            tenant_id=uuid.UUID(tenant_id),
            actor_user_id=actor_user_id,
            action="coparticipation_confirmed",
            entity_type="billing",
            entity_id=billing.id,
            diff={"coparticipation_received": {"before": previous_received, "after": billing.coparticipation_received}},
        )
        return BillingResponse.model_validate(billing)

    async def confirm_clinical_documentation(
        self,
        tenant_id: str,
        actor_user_id: uuid.UUID | None,
        billing_id: uuid.UUID,
        data: BillingClinicalDocumentationConfirmationRequest,
    ) -> BillingResponse:
        """
        Épico F2.3 do Plano Diretor ("Auditoria documental leve —
        prontuário × conta"). Versão RESTRITA (sem NLP semântico):
        confirma (ou não) que existe registro de prescrição/evolução
        sustentando este item OPME. Ver DECISÃO completa em
        044_opme_documentation_confirmation.sql sobre por que o estado
        inicial é NULL, nunca False.
        """
        billing = await self.billing_repo.get_by_id(billing_id)
        if billing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Faturamento não encontrado neste tenant.")
        if billing.item_type != "material_opme":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Este faturamento não é um item de material especial (OPME) — nada para conferir.",
            )
        previous_confirmed = billing.clinical_documentation_confirmed
        billing.clinical_documentation_confirmed = data.found
        billing.clinical_documentation_confirmed_at = datetime.now(timezone.utc)
        billing.clinical_documentation_confirmed_by = actor_user_id
        await self.billing_repo.save(billing)
        await self.audit_repo.record(
            tenant_id=uuid.UUID(tenant_id),
            actor_user_id=actor_user_id,
            action="clinical_documentation_confirmed",
            entity_type="billing",
            entity_id=billing.id,
            diff={
                "clinical_documentation_confirmed": {
                    "before": previous_confirmed,
                    "after": billing.clinical_documentation_confirmed,
                }
            },
        )
        return BillingResponse.model_validate(billing)
