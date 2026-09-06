"""baseline: tour de boas-vindas guiado (onboarding_completed_at)

Revision ID: 0031_user_onboarding
Revises: 0030_platform_feature_usage
Create Date: 2026-09-06

Mesmo padrão de 0001-0030: DDL revisado manualmente em app/sql/, marcado
aqui como baseline.

    psql "$DATABASE_URL" -f app/sql/031_user_onboarding.sql
    alembic stamp 0031_user_onboarding

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente (ADD COLUMN IF NOT EXISTS — auto-idempotente, roda em
todo deploy, sem entrar na _POST_UPGRADE_MARKER_TABLE). Ver DECISÃO
completa em app/sql/031_user_onboarding.sql.
"""
from collections.abc import Sequence

revision: str = "0031_user_onboarding"
down_revision: str | None = "0030_platform_feature_usage"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
