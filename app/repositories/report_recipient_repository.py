"""
app/repositories/report_recipient_repository.py

Repositório recebe a `session` já tenant-aware (RLS garante isolamento —
ver DECISÃO padrão em billing_repository.py).
"""
import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report_recipient import ReportRecipient


class ReportRecipientRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self) -> list[ReportRecipient]:
        stmt = select(ReportRecipient).order_by(ReportRecipient.name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, recipient_id: uuid.UUID) -> ReportRecipient | None:
        stmt = select(ReportRecipient).where(ReportRecipient.id == recipient_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_report_type(self, tenant_id: uuid.UUID, report_type: str) -> list[ReportRecipient]:
        """Destinatários ATIVOS elegíveis para `report_type` — usado pelo
        fan-out do worker/reports sob demanda. `report_types = '{}'`
        (vazio) é o curinga "todos os relatórios" (ver DECISÃO em
        app/sql/009_report_recipients.sql), por isso o OR abaixo aceita
        tanto o array vazio quanto o array que contenha o tipo pedido.
        `tenant_id` é redundante com o RLS da sessão (que já filtra pelo
        tenant corrente) mas é aceito explicitamente para deixar o
        contrato do método autoexplicativo e permitir uso a partir de
        uma sessão `get_db_with_tenant` recém-aberta sem depender de
        estado implícito."""
        stmt = (
            select(ReportRecipient)
            .where(
                ReportRecipient.tenant_id == tenant_id,
                ReportRecipient.active.is_(True),
                or_(
                    ReportRecipient.report_types == [],
                    ReportRecipient.report_types.any(report_type),
                ),
            )
            .order_by(ReportRecipient.name)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add(self, recipient: ReportRecipient) -> ReportRecipient:
        self.session.add(recipient)
        await self.session.flush()
        return recipient

    async def save(self, recipient: ReportRecipient) -> ReportRecipient:
        await self.session.flush()
        return recipient

    async def delete(self, recipient: ReportRecipient) -> None:
        await self.session.delete(recipient)
        await self.session.flush()
