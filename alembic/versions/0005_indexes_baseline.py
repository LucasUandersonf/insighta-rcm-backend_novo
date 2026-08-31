"""baseline: marca os índices de performance (005_performance_indexes.sql)
   como aplicados manualmente

Revision ID: 0005_indexes_baseline
Revises: 0004_no_show_risk
Create Date: 2026-08-28

Mesmo padrão de 0001-0003: índices, embora não sejam objeto de
segurança como RLS, foram adicionados via SQL revisado manualmente
porque acompanham a análise de performance documentada no arquivo
005_performance_indexes.sql — preferimos manter o raciocínio ("por que
este índice, por que agora") junto do SQL, não só no autogenerate.

    psql "$DATABASE_URL" -f app/sql/005_performance_indexes.sql
    alembic stamp 0005_indexes_baseline
"""
from collections.abc import Sequence

revision: str = "0005_indexes_baseline"
down_revision: str | None = "0004_no_show_risk"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
