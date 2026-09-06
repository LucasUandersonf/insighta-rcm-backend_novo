"""baseline: Customer Success orientado a dados (painel interno)

Revision ID: 0026_platform_customer_success
Revises: 0025_webhook_subscriptions
Create Date: 2026-09-06

Mesmo padrão de 0001-0025: DDL revisado manualmente em app/sql/, marcado
aqui como baseline.

    psql "$DATABASE_URL" -f app/sql/026_platform_customer_success.sql
    alembic stamp 0026_platform_customer_success

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente (DROP + CREATE FUNCTION — auto-idempotente, roda em todo
deploy, sem entrar em _POST_UPGRADE_MARKER_TABLE). Ver DECISÃO completa
em app/sql/026_platform_customer_success.sql.
"""
from collections.abc import Sequence

revision: str = "0026_platform_customer_success"
down_revision: str | None = "0025_webhook_subscriptions"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
