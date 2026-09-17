"""
app/repositories/tracked_alert_repository.py — memória contínua dia-a-dia
(Roadmap "Rumo à Nota 9", Fase 3). Ver DECISÃO completa em
app/sql/039_tracked_alerts.sql. Sessão tenant-aware normal (RLS cuida do
isolamento, mesmo padrão de ExecutiveNarrativeRepository).
"""
from datetime import date
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracked_alert import TrackedAlert


class TrackedAlertRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def sync(self, tenant_id: UUID, *, active: dict[str, tuple[str, str]], today: date) -> list[str]:
        """
        `active` = {fact_key: (category, title)} — as situações que os
        insights consideram ativas AGORA (ver DECISÃO em
        smart_insights_engine.derive_fact_key). Duas coisas acontecem:

        1. Toda situação ativa é gravada/atualizada (`last_detected_date`
           = hoje; `resolved_date` volta a NULL se a situação tinha sido
           marcada resolvida e reapareceu — "reabriu").
        2. Toda situação que ESTAVA sem `resolved_date` e não está mais
           em `active` é marcada resolvida HOJE.

        Devolve os títulos marcados resolvidos NESTA chamada — é esse
        conjunto que vira o fato "resolvido desde a última checagem" no
        prompt da narrativa (ver NarrativeFacts.recently_resolved_titles).
        Chamado a cada `get_smart_insights` (não só uma vez por dia): é
        idempotente — uma situação já resolvida hoje não é resolvida de
        novo, então não gera anúncio duplicado na mesma narrativa diária.
        """
        if active:
            stmt = insert(TrackedAlert).values(
                [
                    {
                        "tenant_id": tenant_id,
                        "fact_key": fact_key,
                        "category": category,
                        "title": title,
                        "first_detected_date": today,
                        "last_detected_date": today,
                        "resolved_date": None,
                    }
                    for fact_key, (category, title) in active.items()
                ]
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[TrackedAlert.tenant_id, TrackedAlert.fact_key],
                set_={
                    "category": stmt.excluded.category,
                    "title": stmt.excluded.title,
                    "last_detected_date": stmt.excluded.last_detected_date,
                    "resolved_date": None,
                    "updated_at": func.now(),
                },
            )
            await self.session.execute(stmt)

        active_keys = list(active.keys())
        newly_resolved_stmt = (
            update(TrackedAlert)
            .where(
                TrackedAlert.tenant_id == tenant_id,
                TrackedAlert.resolved_date.is_(None),
                TrackedAlert.fact_key.not_in(active_keys) if active_keys else True,
            )
            .values(resolved_date=today, updated_at=func.now())
            .returning(TrackedAlert.title)
        )
        result = await self.session.execute(newly_resolved_stmt)
        return [row[0] for row in result.all()]

    async def get_resolved_on(self, tenant_id: UUID, *, resolved_date: date) -> list[str]:
        """Títulos resolvidos numa data específica — usado pela narrativa
        quando ela lê o cache do dia (a sincronização já rodou antes,
        numa chamada anterior de get_smart_insights no mesmo dia) e
        precisa saber o que foi resolvido HOJE sem rodar `sync` de novo."""
        stmt = select(TrackedAlert.title).where(
            TrackedAlert.tenant_id == tenant_id, TrackedAlert.resolved_date == resolved_date
        )
        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]
