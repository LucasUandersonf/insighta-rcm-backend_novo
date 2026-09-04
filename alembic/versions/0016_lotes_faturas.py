"""baseline: cria core.lotes, core.faturas e core.guias.lote_id (Fase 2 — Lote + Fatura)

Revision ID: 0016_lotes_faturas
Revises: 0015_billing_guia
Create Date: 2026-09-04

Mesmo padrão de 0001-0015: DDL revisado manualmente em app/sql/, marcado
aqui como baseline — RLS/DDL sensível a produção continua fora do
autogenerate por princípio (ver os outros arquivos desta pasta).

    psql "$DATABASE_URL" -f app/sql/016_lotes_faturas.sql
    alembic stamp 0016_lotes_faturas

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente (guardado pelo marcador de tabela `lotes` em
_POST_UPGRADE_MARKER_TABLE). Ver DECISÃO completa em
app/sql/016_lotes_faturas.sql sobre o que isto fecha (Fase 2 do plano
de adequação ao fluxo real Agendamento -> Atendimento -> Faturamento).
"""
from collections.abc import Sequence

revision: str = "0016_lotes_faturas"
down_revision: str | None = "0015_billing_guia"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
