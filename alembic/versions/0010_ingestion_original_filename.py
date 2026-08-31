"""baseline: nome original do arquivo enviado via upload HTTP — core.ingestion_files.original_filename

Revision ID: 0010_ingestion_original_filename
Revises: 0009_report_recipients
Create Date: 2026-08-29

Mesmo padrão de 0001-0009: DDL revisado manualmente em app/sql/, marcado
aqui como baseline — mesmo sendo só um ADD COLUMN, RLS/DDL sensível a
produção continua fora do autogenerate por princípio (ver os outros
arquivos desta pasta).

    psql "$DATABASE_URL" -f app/sql/010_ingestion_original_filename.sql
    alembic stamp 0010_ingestion_original_filename

Diferença em relação a 006-009: este arquivo SQL usa
`ADD COLUMN IF NOT EXISTS`, então é seguro rodá-lo de novo mesmo que já
tenha rodado — não precisa de marcador de idempotência em
app/scripts/bootstrap_db.py (ver DECISÃO no próprio .sql).
"""
from collections.abc import Sequence

revision: str = "0010_ingestion_original_filename"
down_revision: str | None = "0009_report_recipients"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
