"""baseline: marca o schema de ingestão (003_ingestion_tables.sql) como
   aplicado manualmente, mesma lógica de 0001_baseline_stamp

Revision ID: 0002_ingestion_baseline
Revises: 0001_baseline
Create Date: 2026-08-27

Ver docstring completa de 0001_baseline_stamp.py — mesmo raciocínio:
core.ingestion_files, core.ingestion_raw_rows e
core.marketing_webhook_events têm RLS (FORCE ROW LEVEL SECURITY) e por
isso nasceram como SQL revisado manualmente (app/sql/003_ingestion_tables.sql),
não por autogenerate.

Setup em um banco que já rodou 001+002 e agora precisa do schema de
ingestão:
    psql "$DATABASE_URL" -f app/sql/003_ingestion_tables.sql
    alembic stamp 0002_ingestion_baseline

Em um banco totalmente novo, a ordem completa é:
    psql -f app/sql/001_init_schema.sql
    psql -f app/sql/002_auth_resolver.sql
    psql -f app/sql/003_ingestion_tables.sql
    alembic stamp 0002_ingestion_baseline
    alembic upgrade head   -- aplica migrations "normais" futuras, se houver
"""
from collections.abc import Sequence

revision: str = "0002_ingestion_baseline"
down_revision: str | None = "0001_baseline"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
