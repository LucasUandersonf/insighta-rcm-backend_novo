"""
app/services/report_recipient_service.py

Camada de negócio do cadastro de destinatários de relatório — ver
DECISÃO completa em app/sql/009_report_recipients.sql. A validação
"pelo menos um contato (phone ou email)" já existe em duas camadas
(schema, para o payload de create; CHECK constraint, na tabela) — aqui é
a TERCEIRA camada, aplicada sobre o ESTADO FINAL de um update parcial
(PATCH), o único ponto onde um payload isoladamente válido poderia
produzir um registro inválido (ex: PATCH que zera `phone_whatsapp` num
destinatário que só tinha telefone).
"""
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.models.report_recipient import ReportRecipient
from app.repositories.report_recipient_repository import ReportRecipientRepository
from app.schemas.report_recipient import (
    ReportRecipientCreateRequest,
    ReportRecipientResponse,
    ReportRecipientUpdateRequest,
)


class ReportRecipientService:
    def __init__(self, repo: ReportRecipientRepository):
        self.repo = repo

    async def create_recipient(self, tenant_id: str, data: ReportRecipientCreateRequest) -> ReportRecipientResponse:
        recipient = ReportRecipient(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            name=data.name,
            phone_whatsapp=data.phone_whatsapp,
            email=data.email,
            report_types=data.report_types,
            active=data.active,
        )
        saved = await self.repo.add(recipient)
        return ReportRecipientResponse.model_validate(saved)

    async def list_recipients(self) -> list[ReportRecipientResponse]:
        recipients = await self.repo.list_all()
        return [ReportRecipientResponse.model_validate(r) for r in recipients]

    async def get_recipient(self, recipient_id: uuid.UUID) -> ReportRecipientResponse:
        recipient = await self._get_or_404(recipient_id)
        return ReportRecipientResponse.model_validate(recipient)

    async def update_recipient(
        self, recipient_id: uuid.UUID, data: ReportRecipientUpdateRequest
    ) -> ReportRecipientResponse:
        recipient = await self._get_or_404(recipient_id)

        if data.name is not None:
            recipient.name = data.name
        if data.phone_whatsapp is not None:
            recipient.phone_whatsapp = data.phone_whatsapp
        if data.email is not None:
            recipient.email = data.email
        if data.report_types is not None:
            recipient.report_types = data.report_types
        if data.active is not None:
            recipient.active = data.active

        if not recipient.phone_whatsapp and not recipient.email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Destinatário precisa manter ao menos um contato: phone_whatsapp ou email.",
            )

        # BUG CORRIGIDO — `updated_at` só tinha `onupdate=func.now()`
        # (server-side, embutido no próprio UPDATE): depois do flush, o
        # atributo em memória fica "expirado" e o SQLAlchemy async tenta
        # buscá-lo de volta com uma query implícita fora do greenlet
        # certo, estourando MissingGreenlet bem aqui, no model_validate
        # logo abaixo. Setar explicitamente em Python evita depender
        # desse refresh implícito — mesmo padrão de homologated_at/
        # resolved_at, setados em Python nos outros services deste
        # projeto (nunca via onupdate de banco).
        recipient.updated_at = datetime.now(timezone.utc)
        await self.repo.save(recipient)
        return ReportRecipientResponse.model_validate(recipient)

    async def delete_recipient(self, recipient_id: uuid.UUID) -> None:
        recipient = await self._get_or_404(recipient_id)
        await self.repo.delete(recipient)

    async def _get_or_404(self, recipient_id: uuid.UUID) -> ReportRecipient:
        recipient = await self.repo.get_by_id(recipient_id)
        if recipient is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Destinatário não encontrado neste tenant.")
        return recipient
