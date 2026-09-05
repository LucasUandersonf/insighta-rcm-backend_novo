"""
app/repositories/denial_appeal_repository.py

Repositório recebe a `session` já tenant-aware (RLS garante isolamento —
ver DECISÃO padrão em billing_repository.py).
"""
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.denial_appeal import DenialAppeal


class DenialAppealRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, appeal: DenialAppeal) -> DenialAppeal:
        self.session.add(appeal)
        await self.session.flush()
        return appeal

    async def save(self, appeal: DenialAppeal) -> DenialAppeal:
        await self.session.flush()
        return appeal

    async def get_by_id(self, appeal_id: uuid.UUID) -> DenialAppeal | None:
        stmt = select(DenialAppeal).where(DenialAppeal.id == appeal_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self, *, status_filter: str | None = None) -> list[DenialAppeal]:
        stmt = select(DenialAppeal).order_by(DenialAppeal.deadline_at.asc())
        if status_filter:
            stmt = stmt.where(DenialAppeal.status == status_filter)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_paginated(
        self, *, limit: int, offset: int, status_filter: str | None = None
    ) -> tuple[list[DenialAppeal], int]:
        filters = []
        if status_filter:
            filters.append(DenialAppeal.status == status_filter)

        count_stmt = select(func.count()).select_from(DenialAppeal).where(*filters)
        total = (await self.session.execute(count_stmt)).scalar_one()

        items_stmt = (
            select(DenialAppeal)
            .where(*filters)
            .order_by(DenialAppeal.deadline_at.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(items_stmt)
        return list(result.scalars().all()), total

    async def list_due_within(self, *, as_of: date, horizon_days: int) -> list[DenialAppeal]:
        """Recursos ainda ABERTOS/PROTOCOLADOS cujo prazo vence dentro da
        janela — alimenta o alerta de prazo (Painel de Insights e o KPI
        `appeals_due_soon_count` de executive-summary). Prazo JÁ VENCIDO
        (deadline_at < as_of) também entra — é o caso mais urgente de
        todos, não deveria "sumir" da lista por já ter passado."""

        horizon_date = as_of + timedelta(days=horizon_days)
        stmt = (
            select(DenialAppeal)
            .where(
                DenialAppeal.status.in_(("aberto", "protocolado")),
                DenialAppeal.deadline_at <= horizon_date,
            )
            .order_by(DenialAppeal.deadline_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_due_within(self, *, as_of: date, horizon_days: int) -> int:
        """Mesma janela de list_due_within, mas COUNT no banco em vez de
        materializar as linhas — é o número que alimenta o KPI de
        executive-summary, chamado com muito mais frequência que a tela
        de detalhe da lista."""

        horizon_date = as_of + timedelta(days=horizon_days)
        stmt = select(func.count()).select_from(DenialAppeal).where(
            DenialAppeal.status.in_(("aberto", "protocolado")),
            DenialAppeal.deadline_at <= horizon_date,
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def get_document_context(self, appeal_id: uuid.UUID) -> dict | None:
        """
        Todos os dados factuais para montar o RASCUNHO do documento de
        Recurso de Glosa (ver app/services/denial_appeal_pdf_builder.py) —
        um único SELECT com JOIN em vez de N ORM loads separados
        (appeal -> billing -> appointment -> patient/professional,
        billing -> insurance_plan, billing -> guia), já que este é um
        acesso pontual (1 recurso por vez), não um dashboard.

        `guia_*` e `professional_*` vêm NULL quando o billing não tem
        guia vinculada (billing.guia_id nullable — ver DECISÃO em
        app/sql/015_billing_guia.sql) ou o atendimento não tem
        profissional atribuído — o builder do documento trata isso como
        "não informado", nunca quebra por dado ausente.
        """
        stmt = text(
            """
            SELECT
                da.appeal_type, da.operator_denial_reason, da.denied_at, da.deadline_at, da.status,
                b.charged_value,
                a.procedure_code, a.cid_code, a.scheduled_at AS service_date,
                p.full_name AS patient_name, p.cpf AS patient_cpf,
                prof.full_name AS professional_name, prof.professional_registry,
                ip.display_name AS insurance_plan_name,
                g.tipo AS guia_tipo, g.numero AS guia_numero, g.senha AS guia_senha
            FROM core.denial_appeals da
            JOIN core.billing b ON b.id = da.billing_id
            JOIN core.appointments a ON a.id = b.appointment_id
            JOIN core.patients p ON p.id = a.patient_id
            JOIN core.insurance_plans ip ON ip.id = b.insurance_plan_id
            LEFT JOIN core.professionals prof ON prof.id = a.professional_id
            LEFT JOIN core.guias g ON g.id = b.guia_id
            WHERE da.id = :appeal_id
            """
        )
        result = await self.session.execute(stmt, {"appeal_id": appeal_id})
        row = result.mappings().first()
        return dict(row) if row is not None else None
