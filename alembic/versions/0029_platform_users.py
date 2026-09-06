"""baseline: login individual da equipe da plataforma

Revision ID: 0029_platform_users
Revises: 0028_webhook_delivery_queue
Create Date: 2026-09-06

Mesmo padrão de 0001-0028: DDL revisado manualmente em app/sql/, marcado
aqui como baseline.

    psql "$DATABASE_URL" -f app/sql/029_platform_users.sql
    alembic stamp 0029_platform_users

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente (CREATE TABLE sem IF NOT EXISTS — usa o marcador
"platform_users" em _POST_UPGRADE_MARKER_TABLE). Ver DECISÃO completa
em app/sql/029_platform_users.sql.
"""
from collections.abc import Sequence

revision: str = "0029_platform_users"
down_revision: str | None = "0028_webhook_delivery_queue"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
