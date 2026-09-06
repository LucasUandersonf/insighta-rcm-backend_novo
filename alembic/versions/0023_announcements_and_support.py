"""baseline: Central de Notificações + Central de Ajuda

Revision ID: 0023_announcements_and_support
Revises: 0022_patient_lgpd_erasure
Create Date: 2026-09-06

Mesmo padrão de 0001-0022: DDL revisado manualmente em app/sql/, marcado
aqui como baseline — RLS/DDL sensível a produção continua fora do
autogenerate por princípio (ver os outros arquivos desta pasta).

    psql "$DATABASE_URL" -f app/sql/023_announcements_and_support.sql
    alembic stamp 0023_announcements_and_support

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente (CREATE TABLE sem IF NOT EXISTS — usa o marcador
"platform_announcements" em _POST_UPGRADE_MARKER_TABLE). Ver DECISÃO
completa em app/sql/023_announcements_and_support.sql.
"""
from collections.abc import Sequence

revision: str = "0023_announcements_and_support"
down_revision: str | None = "0022_patient_lgpd_erasure"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
