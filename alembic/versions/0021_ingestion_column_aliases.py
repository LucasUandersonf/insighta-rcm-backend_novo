"""baseline: mapeador automático de coluna (core.ingestion_column_aliases)

Revision ID: 0021_ingestion_column_aliases
Revises: 0020_no_show_thresholds
Create Date: 2026-09-05

Mesmo padrão de 0001-0020: DDL revisado manualmente em app/sql/, marcado
aqui como baseline — RLS/DDL sensível a produção continua fora do
autogenerate por princípio (ver os outros arquivos desta pasta).

    psql "$DATABASE_URL" -f app/sql/021_ingestion_column_aliases.sql
    alembic stamp 0021_ingestion_column_aliases

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente (guardado pelo marcador de tabela
`ingestion_column_aliases` em _POST_UPGRADE_MARKER_TABLE). Ver DECISÃO
completa em app/sql/021_ingestion_column_aliases.sql.
"""
from collections.abc import Sequence

revision: str = "0021_ingestion_column_aliases"
down_revision: str | None = "0020_no_show_thresholds"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
