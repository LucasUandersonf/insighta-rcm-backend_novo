"""
tests/integration/test_health_score_snapshot_repository.py

Testa HealthScoreSnapshotRepository direto contra o Postgres real (RLS
inclusive) — este repositório não é exposto por nenhum endpoint HTTP
(só o job mensal escreve, ver app/worker/health_score_snapshot_job.py;
GET /health-score só lê, já coberto em tests/integration/test_health_score.py),
então o caminho de teste aqui é abrir uma sessão tenant-aware direto,
mesmo padrão que o próprio job usa.
"""
from datetime import date, timedelta
from uuid import UUID

from app.db.session import get_db_with_tenant
from app.repositories.health_score_snapshot_repository import HealthScoreSnapshotRepository


async def test_upsert_is_idempotent_for_the_same_tenant_and_month(tenant_a):
    month = date.today().replace(day=1)
    async for session in get_db_with_tenant(tenant_a):
        repo = HealthScoreSnapshotRepository(session)
        await repo.upsert_snapshot(UUID(tenant_a), snapshot_month=month, score=55.0)
        # Rodar de novo no mesmo mês (retry do job, execução manual) deve
        # SOBRESCREVER a mesma linha, nunca duplicar.
        await repo.upsert_snapshot(UUID(tenant_a), snapshot_month=month, score=70.0)

    async for session in get_db_with_tenant(tenant_a):
        repo = HealthScoreSnapshotRepository(session)
        row = await repo.get_reference_snapshot(on_or_before=date.today())
        assert row is not None
        assert row.score == 70.0


async def test_get_reference_snapshot_never_returns_a_future_snapshot(tenant_a):
    today = date.today()
    async for session in get_db_with_tenant(tenant_a):
        repo = HealthScoreSnapshotRepository(session)
        await repo.upsert_snapshot(UUID(tenant_a), snapshot_month=today.replace(day=1), score=99.0)

    async for session in get_db_with_tenant(tenant_a):
        repo = HealthScoreSnapshotRepository(session)
        row = await repo.get_reference_snapshot(on_or_before=today - timedelta(days=95))
        assert row is None  # o único snapshot que existe é de HOJE, não de referência


async def test_get_reference_snapshot_isolates_between_tenants(tenant_a, tenant_b):
    month = date.today().replace(day=1)
    async for session in get_db_with_tenant(tenant_a):
        await HealthScoreSnapshotRepository(session).upsert_snapshot(UUID(tenant_a), snapshot_month=month, score=42.0)

    async for session in get_db_with_tenant(tenant_b):
        row = await HealthScoreSnapshotRepository(session).get_reference_snapshot(on_or_before=date.today())
        assert row is None  # RLS — tenant B nunca vê o snapshot do tenant A
