"""baseline: webhooks outbound (Slack/CRM/Zapier)

Revision ID: 0025_webhook_subscriptions
Revises: 0024_api_key_resolver
Create Date: 2026-09-06

Mesmo padrão de 0001-0024: DDL revisado manualmente em app/sql/, marcado
aqui como baseline.

    psql "$DATABASE_URL" -f app/sql/025_webhook_subscriptions.sql
    alembic stamp 0025_webhook_subscriptions

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente (CREATE TABLE sem IF NOT EXISTS — usa o marcador
"webhook_subscriptions" em _POST_UPGRADE_MARKER_TABLE). Ver DECISÃO
completa em app/sql/025_webhook_subscriptions.sql.
"""
from collections.abc import Sequence

revision: str = "0025_webhook_subscriptions"
down_revision: str | None = "0024_api_key_resolver"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
