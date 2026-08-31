"""baseline: Destinatários de relatório (multi-recipient) — report_recipients

Revision ID: 0009_report_recipients
Revises: 0008_denial_appeals
Create Date: 2026-08-29

Mesmo padrão de 0001-0008: DDL revisado manualmente em app/sql/, marcado
aqui como baseline — RLS é sensível demais para autogenerate.

    psql "$DATABASE_URL" -f app/sql/009_report_recipients.sql
    alembic stamp 0009_report_recipients
"""
from collections.abc import Sequence

revision: str = "0009_report_recipients"
down_revision: str | None = "0008_denial_appeals"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
