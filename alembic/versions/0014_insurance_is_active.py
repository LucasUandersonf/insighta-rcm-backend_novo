"""baseline: adiciona is_active a core.insurance_companies e core.insurance_plans

Revision ID: 0014_insurance_is_active
Revises: 0013_fix_plan_tier_check
Create Date: 2026-09-04

Mesmo padrão de 0001-0013: DDL revisado manualmente em app/sql/, marcado
aqui como baseline — RLS/DDL sensível a produção continua fora do
autogenerate por princípio (ver os outros arquivos desta pasta).

    psql "$DATABASE_URL" -f app/sql/014_insurance_is_active.sql
    alembic stamp 0014_insurance_is_active

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente a cada deploy (auto-idempotente: ADD COLUMN IF NOT
EXISTS) — os dois comandos acima só importam para quem sobe o banco
manualmente em outro ambiente. Ver DECISÃO completa em
app/sql/014_insurance_is_active.sql sobre o gap que isto fecha.
"""
from collections.abc import Sequence

revision: str = "0014_insurance_is_active"
down_revision: str | None = "0013_fix_plan_tier_check"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
