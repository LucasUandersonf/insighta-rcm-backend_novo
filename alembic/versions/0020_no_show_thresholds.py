"""baseline: limiares de risco de falta configuráveis por tenant

Revision ID: 0020_no_show_thresholds
Revises: 0019_agenda_ingestion
Create Date: 2026-09-04

Mesmo padrão de 0001-0019: DDL revisado manualmente em app/sql/, marcado
aqui como baseline — RLS/DDL sensível a produção continua fora do
autogenerate por princípio (ver os outros arquivos desta pasta).

    psql "$DATABASE_URL" -f app/sql/020_no_show_thresholds.sql
    alembic stamp 0020_no_show_thresholds

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente (auto-idempotente por construção — ADD COLUMN IF NOT
EXISTS — não precisa de marcador). Ver DECISÃO completa em
app/sql/020_no_show_thresholds.sql.
"""
from collections.abc import Sequence

revision: str = "0020_no_show_thresholds"
down_revision: str | None = "0019_agenda_ingestion"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
