"""baseline: Recurso de Glosa (conformidade ANS) — denial_appeals,
   denial_appeal_attachments, insurance_companies.default_appeal_deadline_days

Revision ID: 0008_denial_appeals
Revises: 0007_contract_intelligence
Create Date: 2026-08-29

Mesmo padrão de 0001-0007: DDL revisado manualmente em app/sql/, marcado
aqui como baseline — RLS e o CHECK de máquina de estados são sensíveis
demais para autogenerate.

    psql "$DATABASE_URL" -f app/sql/008_denial_appeals.sql
    alembic stamp 0008_denial_appeals
"""
from collections.abc import Sequence

revision: str = "0008_denial_appeals"
down_revision: str | None = "0007_contract_intelligence"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
