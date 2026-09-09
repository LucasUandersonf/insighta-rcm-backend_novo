"""baseline: tendência da Nota de Saúde Financeira (health_score_snapshots)

Revision ID: 0034_health_score_snapshots
Revises: 0031_user_onboarding
Create Date: 2026-09-09

Mesmo padrão de 0001-0031: DDL revisado manualmente em app/sql/, marcado
aqui como baseline. 032/033 (network_benchmark/network_contract_price_benchmark)
ficam de fora desta cadeia de propósito — são funções DROP+CREATE
auto-idempotentes, sem tabela/coluna nova, então não precisam de um
baseline Alembic (ver DECISÃO em bootstrap_db.py, _POST_UPGRADE_MARKER_TABLE).

    psql "$DATABASE_URL" -f app/sql/034_health_score_snapshots.sql
    alembic stamp 0034_health_score_snapshots

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente (CREATE TABLE sem IF NOT EXISTS — usa o marcador
"health_score_snapshots" em _POST_UPGRADE_MARKER_TABLE). Ver DECISÃO
completa em app/sql/034_health_score_snapshots.sql.
"""
from collections.abc import Sequence

revision: str = "0034_health_score_snapshots"
down_revision: str | None = "0031_user_onboarding"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
