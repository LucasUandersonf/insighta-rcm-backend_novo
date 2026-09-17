"""
app/repositories/executive_narrative_repository.py — cache diário do
resumo executivo narrado por IA (ver DECISÃO em
app/sql/038_executive_narratives.sql). Sessão tenant-aware normal (RLS
cuida do isolamento, mesmo padrão de HealthScoreSnapshotRepository).
"""
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.executive_narrative import ExecutiveNarrative


@dataclass
class ExecutiveNarrativeRow:
    digest_date: date
    period_start: date
    period_end: date
    narrative_text: str
    model: str


class ExecutiveNarrativeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_for_date(self, digest_date: date) -> ExecutiveNarrativeRow | None:
        """A narrativa já gerada para este dia, se houver — quem chama
        (ExecutiveNarrativeService) só gera uma nova via IA quando isto
        devolve None."""
        stmt = select(ExecutiveNarrative).where(ExecutiveNarrative.digest_date == digest_date)
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return ExecutiveNarrativeRow(
            digest_date=row.digest_date,
            period_start=row.period_start,
            period_end=row.period_end,
            narrative_text=row.narrative_text,
            model=row.model,
        )

    async def upsert(
        self, tenant_id: UUID, *, digest_date: date, period_start: date, period_end: date, narrative_text: str, model: str
    ) -> None:
        """ON CONFLICT em vez de checar antes de gravar (mesmo raciocínio
        de HealthScoreSnapshotRepository.upsert_snapshot): duas
        requisições concorrentes no primeiro acesso do dia (dois
        gestores abrindo a tela ao mesmo tempo) podem gerar a narrativa
        em paralelo — sem UPSERT, a segunda escrita quebraria a
        constraint UNIQUE(tenant_id, digest_date). Mantém a ÚLTIMA
        geração bem-sucedida, não a primeira (raro o bastante para não
        importar qual das duas "vence")."""
        stmt = insert(ExecutiveNarrative).values(
            tenant_id=tenant_id,
            digest_date=digest_date,
            period_start=period_start,
            period_end=period_end,
            narrative_text=narrative_text,
            model=model,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ExecutiveNarrative.tenant_id, ExecutiveNarrative.digest_date],
            set_={
                "period_start": stmt.excluded.period_start,
                "period_end": stmt.excluded.period_end,
                "narrative_text": stmt.excluded.narrative_text,
                "model": stmt.excluded.model,
            },
        )
        await self.session.execute(stmt)
