import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.insight_outcome import InsightOutcome


class InsightOutcomeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, outcome_id: uuid.UUID) -> InsightOutcome | None:
        stmt = select(InsightOutcome).where(InsightOutcome.id == outcome_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_paginated(
        self, *, limit: int, offset: int, status_filter: str | None = None, assigned_to: uuid.UUID | None = None
    ) -> tuple[list[InsightOutcome], int]:
        items_stmt = select(InsightOutcome).order_by(InsightOutcome.created_at.desc()).limit(limit).offset(offset)
        count_stmt = select(func.count()).select_from(InsightOutcome)
        if status_filter is not None:
            items_stmt = items_stmt.where(InsightOutcome.status == status_filter)
            count_stmt = count_stmt.where(InsightOutcome.status == status_filter)
        if assigned_to is not None:
            items_stmt = items_stmt.where(InsightOutcome.assigned_to == assigned_to)
            count_stmt = count_stmt.where(InsightOutcome.assigned_to == assigned_to)
        items = list((await self.session.execute(items_stmt)).scalars().all())
        total = (await self.session.execute(count_stmt)).scalar_one()
        return items, total

    async def list_resolved_and_reevaluated(self, *, limit: int = 50) -> list[InsightOutcome]:
        """Painel "Insights que valeram a pena" (F1.2) — só outcomes já
        REAVALIADOS, mais recentes primeiro."""
        stmt = (
            select(InsightOutcome)
            .where(InsightOutcome.status == "resolvido", InsightOutcome.reevaluated_at.is_not(None))
            .order_by(InsightOutcome.reevaluated_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def sum_realized_delta(self) -> tuple[float, int]:
        """
        Épico F4.4 do Plano Diretor ("Prova de ROI do próprio produto")
        — "ganho de renegociação realizado" generalizado pra QUALQUER
        categoria (mesmo espírito de InsightOutcomesRealizedSummary,
        ver DECISÃO em app/services/insight_outcome_service.py), mas
        SOMA no banco em vez de materializar linhas (list_resolved_and_reevaluated
        tem limit=50, adequado pra exibir uma lista, NUNCA pra somar um
        total: com mais de 50 outcomes reavaliados, o total ficaria
        errado por baixo). Só conta outcomes JÁ REAVALIADOS com os dois
        valores presentes — mesmo critério de "nunca soma uma promessa
        ainda não conferida" do resto do produto.

        Retorna (soma_do_ganho_realizado, quantidade_de_outcomes).
        """
        delta_expr = InsightOutcome.financial_impact_snapshot - InsightOutcome.resolved_metric_value
        stmt = select(func.coalesce(func.sum(delta_expr), 0), func.count()).where(
            InsightOutcome.status == "resolvido",
            InsightOutcome.reevaluated_at.is_not(None),
            InsightOutcome.financial_impact_snapshot.is_not(None),
            InsightOutcome.resolved_metric_value.is_not(None),
        )
        total_delta, count = (await self.session.execute(stmt)).one()
        return float(total_delta), int(count)

    async def list_pending_reevaluation(self, *, as_of: datetime, min_days_since_resolved: int) -> list[InsightOutcome]:
        """Job periódico (F1.2, app/worker/insight_outcome_reevaluation_job.py):
        outcomes marcados 'resolvido' há pelo menos `min_days_since_resolved`
        dias e ainda não reavaliados. SEM filtro de tenant — o job usa
        `get_db_with_tenant` por tenant (mesmo padrão dos outros jobs),
        então este método já roda dentro do contexto RLS certo."""
        cutoff = as_of.timestamp() - min_days_since_resolved * 86400
        cutoff_dt = datetime.fromtimestamp(cutoff, tz=timezone.utc)
        stmt = select(InsightOutcome).where(
            InsightOutcome.status == "resolvido",
            InsightOutcome.reevaluated_at.is_(None),
            InsightOutcome.resolved_at.is_not(None),
            InsightOutcome.resolved_at <= cutoff_dt,
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add(self, outcome: InsightOutcome) -> InsightOutcome:
        self.session.add(outcome)
        await self.session.flush()
        return outcome

    async def save(self, outcome: InsightOutcome) -> InsightOutcome:
        outcome.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return outcome
