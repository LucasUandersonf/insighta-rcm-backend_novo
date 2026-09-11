"""baseline: correções da Auditoria de Templates e Insights (índice member_card_number)

Revision ID: 0037_billing_glosa_hardening
Revises: 0036_billing_appt_ext_fields
Create Date: 2026-09-11

Mesmo padrão de 0001-0036: DDL revisado manualmente em app/sql/, marcado
aqui como baseline.

    psql "$DATABASE_URL" -f app/sql/037_billing_glosa_hardening.sql
    alembic stamp 0037_billing_glosa_hardening

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente (CREATE INDEX IF NOT EXISTS — auto-idempotente, roda em
todo deploy, sem entrar na _POST_UPGRADE_MARKER_TABLE). Ver DECISÃO
completa em app/sql/037_billing_glosa_hardening.sql.
"""
from collections.abc import Sequence

revision: str = "0037_billing_glosa_hardening"
down_revision: str | None = "0036_billing_appt_ext_fields"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
