"""baseline: Parser Inteligente de Contratos — insurance_companies,
   contracts (cabeçalho) + contract_items, billing.received_value

Revision ID: 0007_contract_intelligence
Revises: 0006_platform_admin
Create Date: 2026-08-29

Mesmo padrão de 0001-0006: DDL revisado manualmente em app/sql/, marcado
aqui como baseline — RLS e a quebra de contracts em cabeçalho+itens são
mudanças estruturais sensíveis demais para autogenerate.

    psql "$DATABASE_URL" -f app/sql/007_contract_intelligence.sql
    alembic stamp 0007_contract_intelligence
"""
from collections.abc import Sequence

revision: str = "0007_contract_intelligence"
down_revision: str | None = "0006_platform_admin"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
