"""baseline: alertas proativos de Customer Success

Revision ID: 0027_platform_risk_alerts
Revises: 0026_platform_customer_success
Create Date: 2026-09-06

Mesmo padrão de 0001-0026: DDL revisado manualmente em app/sql/, marcado
aqui como baseline.

    psql "$DATABASE_URL" -f app/sql/027_platform_risk_alerts.sql
    alembic stamp 0027_platform_risk_alerts

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente (CREATE TABLE sem IF NOT EXISTS — usa o marcador
"platform_risk_alerts" em _POST_UPGRADE_MARKER_TABLE). Ver DECISÃO
completa em app/sql/027_platform_risk_alerts.sql.
"""
from collections.abc import Sequence

revision: str = "0027_platform_risk_alerts"
down_revision: str | None = "0026_platform_customer_success"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
