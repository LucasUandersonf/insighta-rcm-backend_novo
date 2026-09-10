"""baseline: terceiro Template de Integração "Glosa" (ingestion_files_data_type_check)

Revision ID: 0035_glosa_ingestion
Revises: 0034_health_score_snapshots
Create Date: 2026-09-10

Mesmo padrão de 0001-0034: DDL revisado manualmente em app/sql/, marcado
aqui como baseline.

    psql "$DATABASE_URL" -f app/sql/035_glosa_ingestion.sql
    alembic stamp 0035_glosa_ingestion

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente (DROP CONSTRAINT IF EXISTS + ADD — auto-idempotente, roda
em todo deploy, sem entrar na _POST_UPGRADE_MARKER_TABLE). Ver DECISÃO
completa em app/sql/035_glosa_ingestion.sql.
"""
from collections.abc import Sequence

revision: str = "0035_glosa_ingestion"
down_revision: str | None = "0034_health_score_snapshots"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
