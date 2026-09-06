"""baseline: fila de retentativa para webhooks

Revision ID: 0028_webhook_delivery_queue
Revises: 0027_platform_risk_alerts
Create Date: 2026-09-06

Mesmo padrão de 0001-0027: DDL revisado manualmente em app/sql/, marcado
aqui como baseline.

    psql "$DATABASE_URL" -f app/sql/028_webhook_delivery_queue.sql
    alembic stamp 0028_webhook_delivery_queue

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente (CREATE TABLE sem IF NOT EXISTS — usa o marcador
"webhook_delivery_queue" em _POST_UPGRADE_MARKER_TABLE). Ver DECISÃO
completa em app/sql/028_webhook_delivery_queue.sql.
"""
from collections.abc import Sequence

revision: str = "0028_webhook_delivery_queue"
down_revision: str | None = "0027_platform_risk_alerts"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
