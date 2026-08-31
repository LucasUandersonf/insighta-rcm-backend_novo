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
from app.repositories.billing_repository import BillingRepository
from app.repositories.contract_item_repository import ContractItemRepository
from app.schemas.billing import BillingCreateRequest, BillingResponse, BillingSettleRequest
from app.schemas.pagination import PaginatedResponse
from app.services.denial_risk_engine import assess


class BillingService:
    def __init__(
        self,
        billing_repo: BillingRepository,
        appointment_repo: AppointmentRepository,
        contract_item_repo: ContractItemRepository,
    ):
        self.billing_repo = billing_repo
        self.appointment_repo = appointment_repo
        self.contract_item_repo = contract_item_repo

    async def create_billing(self, tenant_id: str, data: BillingCreateRequest) -> BillingResponse:
        # Mesma observação de sempre: se appointment_id pertencer a outro
        # tenant, o RLS já o esconde daqui — "não encontrado" cobre os
        # dois casos (não existe / não é deste tenant) sem checagem extra.
        appointment = await self.appointment_repo.get_by_id(data.appointment_id)
        if appointment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Atendimento não encontrado neste tenant.",
            )

        contract_item = None
        if appointment.procedure_code:
            contract_item = await self.contract_item_repo.find_agreed_price(
                insurance_plan_id=data.insurance_plan_id,
                tuss_code=appointment.procedure_code,
            )

        risk = assess(appointment, contract_item, data.charged_value)

        billing = Billing(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            appointment_id=data.appointment_id,
            insurance_plan_id=data.insurance_plan_id,
            charged_value=data.charged_value,
            # Regra de negócio do briefing: risco alto barra o envio
            # automaticamente -> status "held_for_review" em vez de "pending".
            status="held_for_review" if risk.should_hold_for_review else "pending",
            denial_risk_level=risk.level,
            denial_reasons=risk.reasons,
            value_saved_by_correction=float(risk.value_saved_by_correction),
        )
        saved = await self.billing_repo.add(billing)
        return BillingResponse.model_validate(saved)

    async def list_high_risk(self) -> list[BillingResponse]:
        items = await self.billing_repo.list_high_risk()
        return [BillingResponse.model_validate(i) for i in items]

    async def list_high_risk_paginated(self, *, limit: int, offset: int) -> PaginatedResponse[BillingResponse]:
        items, total = await self.billing_repo.list_high_risk_paginated(limit=limit, offset=offset)
        return PaginatedResponse(
            items=[BillingResponse.model_validate(i) for i in items], total=total, limit=limit, offset=offset
        )

    async def settle_billing(self, billing_id: uuid.UUID, data: BillingSettleRequest) -> BillingResponse:
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
        billing.received_value = data.received_value
        billing.settled_at = datetime.now(timezone.utc)
        billing.status = "paid"
        await self.billing_repo.save(billing)
        return BillingResponse.model_validate(billing)
