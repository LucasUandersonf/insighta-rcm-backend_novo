"""baseline: direito de eliminação do titular (LGPD art. 18, VI)

Revision ID: 0022_patient_lgpd_erasure
Revises: 0021_ingestion_column_aliases
Create Date: 2026-09-06

Mesmo padrão de 0001-0021: DDL revisado manualmente em app/sql/, marcado
aqui como baseline — RLS/DDL sensível a produção continua fora do
autogenerate por princípio (ver os outros arquivos desta pasta).

    psql "$DATABASE_URL" -f app/sql/022_patient_lgpd_erasure.sql
    alembic stamp 0022_patient_lgpd_erasure

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente (auto-idempotente por construção — ADD COLUMN IF NOT
EXISTS — não precisa de marcador). Ver DECISÃO completa em
app/sql/022_patient_lgpd_erasure.sql.
"""
from collections.abc import Sequence

revision: str = "0022_patient_lgpd_erasure"
down_revision: str | None = "0021_ingestion_column_aliases"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
