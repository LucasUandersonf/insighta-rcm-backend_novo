"""
app/repositories/health_score_snapshot_repository.py — histórico da
Nota de Saúde Financeira (ver DECISÃO em app/sql/034_health_score_snapshots.sql).
Sessão tenant-aware normal (RLS cuida do isolamento, mesmo padrão de
ReportingRepository/AnalyticsRepository) — diferente de
NetworkBenchmarkRepository/ContractPriceBenchmarkRepository, que
precisam escapar do RLS de propósito.
"""
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health_score_snapshot import HealthScoreSnapshot


@dataclass
class HealthScoreSnapshotRow:
    snapshot_month: date
    score: float | None


class HealthScoreSnapshotRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert_snapshot(self, tenant_id: UUID, *, snapshot_month: date, score: float | None) -> None:
        """Grava (ou substitui) a fotografia do mês — chamado 1x por mês
        pelo job (ver app/worker/health_score_snapshot_job.py). ON
        CONFLICT em vez de SELECT+INSERT/UPDATE: o job pode rodar mais de
        uma vez no mesmo mês (retry, execução manual) sem duplicar linha
        nem exigir uma consulta extra antes de escrever."""
        stmt = insert(HealthScoreSnapshot).values(
            tenant_id=tenant_id, snapshot_month=snapshot_month, score=score
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[HealthScoreSnapshot.tenant_id, HealthScoreSnapshot.snapshot_month],
            set_={"score": stmt.excluded.score},
        )
        await self.session.execute(stmt)

    async def get_reference_snapshot(self, *, on_or_before: date) -> HealthScoreSnapshotRow | None:
        """O snapshot mais RECENTE cujo mês é <= `on_or_before` — usado
        para achar "a nota de ~90 dias atrás": o chamador passa
        `today - 90 dias` e recebe a fotografia mais próxima daquele
        marco (nunca uma futura, nunca inventa um ponto entre dois
        snapshots reais). Sem filtro de tenant_id explícito — mesma
        convenção do resto do serviço nesta sessão tenant-aware (ver
        DbSession/get_db_with_tenant): o RLS já restringe ao tenant
        atual, FORCE ROW LEVEL SECURITY inclusive para o dono da tabela."""
        stmt = (
            select(HealthScoreSnapshot)
            .where(HealthScoreSnapshot.snapshot_month <= on_or_before)
            .order_by(HealthScoreSnapshot.snapshot_month.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return HealthScoreSnapshotRow(
            snapshot_month=row.snapshot_month, score=float(row.score) if row.score is not None else None
        )
