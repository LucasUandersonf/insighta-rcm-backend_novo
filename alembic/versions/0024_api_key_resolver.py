"""baseline: resolver de API key cross-tenant (SECURITY DEFINER)

Revision ID: 0024_api_key_resolver
Revises: 0023_announcements_and_support
Create Date: 2026-09-06

Mesmo padrão de 0001-0023: DDL revisado manualmente em app/sql/, marcado
aqui como baseline.

    psql "$DATABASE_URL" -f app/sql/024_api_key_resolver.sql
    alembic stamp 0024_api_key_resolver

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente (DROP + CREATE, auto-idempotente). Ver DECISÃO completa
em app/sql/024_api_key_resolver.sql.
"""
from collections.abc import Sequence

revision: str = "0024_api_key_resolver"
down_revision: str | None = "0023_announcements_and_support"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
