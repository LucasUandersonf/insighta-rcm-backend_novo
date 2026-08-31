"""baseline: fundação de administração da plataforma (006_platform_admin.sql)
   — gestão de usuários (must_change_password) + tabela api_keys

Revision ID: 0006_platform_admin
Revises: 0005_indexes_baseline
Create Date: 2026-08-28

Mesmo padrão de 0001-0005: DDL revisado manualmente em app/sql/, marcado
aqui como baseline em vez de gerado por autogenerate — RLS e a lógica de
hashing de chave de API são DDL/decisões de segurança sensíveis demais
para confiar no autogenerate do Alembic.

    psql "$DATABASE_URL" -f app/sql/006_platform_admin.sql
    alembic stamp 0006_platform_admin
"""
from collections.abc import Sequence

revision: str = "0006_platform_admin"
down_revision: str | None = "0005_indexes_baseline"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
