"""baseline: meta de faturamento anual manual — core.tenants.annual_revenue_goal

Revision ID: 0011_annual_revenue_goal
Revises: 0010_ingestion_original_filename
Create Date: 2026-08-29

Mesmo padrão de 0001-0010: DDL revisado manualmente em app/sql/, marcado
aqui como baseline — RLS/DDL sensível a produção continua fora do
autogenerate por princípio (ver os outros arquivos desta pasta).

    psql "$DATABASE_URL" -f app/sql/011_annual_revenue_goal.sql
    alembic stamp 0011_annual_revenue_goal

Mesma situação de 0010: `ADD COLUMN IF NOT EXISTS`, seguro rodar de novo
— sem marcador de idempotência em app/scripts/bootstrap_db.py (ver
DECISÃO no próprio .sql).
"""
from collections.abc import Sequence

revision: str = "0011_annual_revenue_goal"
down_revision: str | None = "0010_ingestion_original_filename"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
