"""baseline: cria core.glosas (Fase 3 — Conciliação de Glosa real)

Revision ID: 0017_glosas
Revises: 0016_lotes_faturas
Create Date: 2026-09-04

Mesmo padrão de 0001-0016: DDL revisado manualmente em app/sql/, marcado
aqui como baseline — RLS/DDL sensível a produção continua fora do
autogenerate por princípio (ver os outros arquivos desta pasta).

    psql "$DATABASE_URL" -f app/sql/017_glosas.sql
    alembic stamp 0017_glosas

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente (guardado pelo marcador de tabela `glosas` em
_POST_UPGRADE_MARKER_TABLE). Ver DECISÃO completa em
app/sql/017_glosas.sql sobre o que isto fecha (Fase 3 do plano de
adequação ao fluxo real Agendamento -> Atendimento -> Faturamento).
"""
from collections.abc import Sequence

revision: str = "0017_glosas"
down_revision: str | None = "0016_lotes_faturas"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
