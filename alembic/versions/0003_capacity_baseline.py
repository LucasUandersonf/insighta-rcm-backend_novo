"""baseline: marca professionals/professional_availability
   (004_capacity_management.sql) como aplicado manualmente

Revision ID: 0003_capacity_baseline
Revises: 0002_ingestion_baseline
Create Date: 2026-08-27

Mesmo raciocínio de 0001/0002: FORCE ROW LEVEL SECURITY nasce como SQL
revisado manualmente, não por autogenerate.

    psql "$DATABASE_URL" -f app/sql/004_capacity_management.sql
    alembic stamp 0003_capacity_baseline
"""
from collections.abc import Sequence

revision: str = "0003_capacity_baseline"
down_revision: str | None = "0002_ingestion_baseline"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
