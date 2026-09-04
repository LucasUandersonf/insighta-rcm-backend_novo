"""baseline: Template de Integração "Agenda" (external_id + data_type)

Revision ID: 0019_agenda_ingestion
Revises: 0018_locais_tipo_paciente
Create Date: 2026-09-04

Mesmo padrão de 0001-0018: DDL revisado manualmente em app/sql/, marcado
aqui como baseline — RLS/DDL sensível a produção continua fora do
autogenerate por princípio (ver os outros arquivos desta pasta).

    psql "$DATABASE_URL" -f app/sql/019_agenda_ingestion.sql
    alembic stamp 0019_agenda_ingestion

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente (auto-idempotente por construção — ADD COLUMN IF NOT
EXISTS + DROP/ADD CONSTRAINT — não precisa de marcador). Ver DECISÃO
completa em app/sql/019_agenda_ingestion.sql sobre o que isto fecha
(pivô de estratégia: templates de integração canônicos em vez de
telas de CRUD manual para o cliente entregar dado).
"""
from collections.abc import Sequence

revision: str = "0019_agenda_ingestion"
down_revision: str | None = "0018_locais_tipo_paciente"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
