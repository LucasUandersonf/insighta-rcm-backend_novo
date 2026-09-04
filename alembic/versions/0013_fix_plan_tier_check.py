"""baseline: corrige CHECK constraint de core.tenants.plan_tier ('pro' -> 'professional')

Revision ID: 0013_fix_plan_tier_check
Revises: 0012_password_reset
Create Date: 2026-09-04

Mesmo padrão de 0001-0012: DDL revisado manualmente em app/sql/, marcado
aqui como baseline — RLS/DDL sensível a produção continua fora do
autogenerate por princípio (ver os outros arquivos desta pasta).

    psql "$DATABASE_URL" -f app/sql/013_fix_plan_tier_check.sql
    alembic stamp 0013_fix_plan_tier_check

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente a cada deploy (auto-idempotente: DROP CONSTRAINT IF
EXISTS + ADD CONSTRAINT) — os dois comandos acima só importam para quem
sobe o banco manualmente em outro ambiente. Ver DECISÃO completa em
app/sql/013_fix_plan_tier_check.sql sobre o bug que isto corrige.
"""
from collections.abc import Sequence

revision: str = "0013_fix_plan_tier_check"
down_revision: str | None = "0012_password_reset"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
