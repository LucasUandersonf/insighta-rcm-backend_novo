"""
app/repositories/glosa_repository.py

Glosa REAL — ver DECISÃO completa em app/models/glosa.py. `reconciliation`
usa SQL cru com LEFT JOIN (mesmo estilo de
analytics_repository.financial_hole_total) para comparar, num só round-trip
ao banco, o que o motor de risco PREVIU (Billing.denial_risk_level) contra
o que a operadora REALMENTE devolveu (existência de Glosa para aquele
billing_id) — ver DECISÃO completa em app/schemas/glosa.py.
"""
import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.glosa import Glosa


def _bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(date_from, time.min, tzinfo=timezone.utc),
        datetime.combine(date_to, time.max, tzinfo=timezone.utc),
    )


class GlosaRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, glosa_id: uuid.UUID) -> Glosa | None:
        stmt = select(Glosa).where(Glosa.id == glosa_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_paginated(self, *, limit: int, offset: int) -> tuple[list[Glosa], int]:
        items_stmt = select(Glosa).order_by(Glosa.created_at.desc()).limit(limit).offset(offset)
        items = list((await self.session.execute(items_stmt)).scalars().all())
        total = (await self.session.execute(select(func.count()).select_from(Glosa))).scalar_one()
        return items, total

    async def add(self, glosa: Glosa) -> Glosa:
        self.session.add(glosa)
        await self.session.flush()
        return glosa

    async def reconciliation_by_risk_level(self, date_from: date, date_to: date) -> list[tuple[str, int, int, float]]:
        """
        Para cada denial_risk_level PREVISTO na criação do billing
        (Billing.created_at no período), conta quantos billings existem
        naquele nível e quantos deles têm PELO MENOS UMA glosa real
        registrada — LEFT JOIN + COUNT(DISTINCT ...) para não contar
        errado quando um billing tem mais de uma glosa (ex.: item
        parcialmente pago em duas remessas).

        Devolve (level, billing_count, glosado_count, valor_glosado_total).
        """
        start, end = _bounds(date_from, date_to)
        stmt = text(
            """
            SELECT
                b.denial_risk_level,
                COUNT(DISTINCT b.id) AS billing_count,
                COUNT(DISTINCT g.billing_id) AS glosado_count,
                COALESCE(SUM(g.valor_glosado), 0) AS valor_glosado_total
            FROM core.billing b
            LEFT JOIN core.glosas g ON g.billing_id = b.id
            WHERE b.created_at >= :start AND b.created_at <= :end
            GROUP BY b.denial_risk_level
            """
        )
        result = await self.session.execute(stmt, {"start": start, "end": end})
        return [(level, int(billing_count), int(glosado_count), float(valor_total)) for level, billing_count, glosado_count, valor_total in result.all()]
