"""baseline: uso por recurso no painel de Customer Success

Revision ID: 0030_platform_feature_usage
Revises: 0029_platform_users
Create Date: 2026-09-06

Mesmo padrão de 0001-0029: DDL revisado manualmente em app/sql/, marcado
aqui como baseline.

    psql "$DATABASE_URL" -f app/sql/030_platform_feature_usage.sql
    alembic stamp 0030_platform_feature_usage

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente (DROP + CREATE FUNCTION — auto-idempotente, roda em todo
deploy, sem entrar na _POST_UPGRADE_MARKER_TABLE). Ver DECISÃO completa
em app/sql/030_platform_feature_usage.sql.
"""
from collections.abc import Sequence

revision: str = "0030_platform_feature_usage"
down_revision: str | None = "0029_platform_users"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
