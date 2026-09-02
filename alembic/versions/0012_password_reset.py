"""baseline: cadastro público + recuperação de senha — core.password_reset_tokens

Revision ID: 0012_password_reset
Revises: 0011_annual_revenue_goal
Create Date: 2026-09-02

Mesmo padrão de 0001-0011: DDL revisado manualmente em app/sql/, marcado
aqui como baseline — RLS/DDL sensível a produção continua fora do
autogenerate por princípio (ver os outros arquivos desta pasta).

    psql "$DATABASE_URL" -f app/sql/012_password_reset.sql
    alembic stamp 0012_password_reset

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente a cada deploy (ele é auto-idempotente: CREATE TABLE IF
NOT EXISTS + DROP/CREATE FUNCTION) — os dois comandos acima só importam
para quem sobe o banco manualmente em outro ambiente.
"""
from collections.abc import Sequence

revision: str = "0012_password_reset"
down_revision: str | None = "0011_annual_revenue_goal"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
