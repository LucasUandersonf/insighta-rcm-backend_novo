"""baseline: cria core.guias e core.billing.guia_id (Fase 1 — Guia TISS)

Revision ID: 0015_billing_guia
Revises: 0014_insurance_is_active
Create Date: 2026-09-04

Mesmo padrão de 0001-0014: DDL revisado manualmente em app/sql/, marcado
aqui como baseline — RLS/DDL sensível a produção continua fora do
autogenerate por princípio (ver os outros arquivos desta pasta).

    psql "$DATABASE_URL" -f app/sql/015_billing_guia.sql
    alembic stamp 0015_billing_guia

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente (guardado pelo marcador de tabela `guias` em
_POST_UPGRADE_MARKER_TABLE — roda uma vez só, não é auto-idempotente
porque CREATE TABLE não tem IF NOT EXISTS). Ver DECISÃO completa em
app/sql/015_billing_guia.sql sobre o que isto começa a fechar (o plano
de adequação ao fluxo real Agendamento -> Atendimento -> Faturamento).
"""
from collections.abc import Sequence

revision: str = "0015_billing_guia"
down_revision: str | None = "0014_insurance_is_active"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
