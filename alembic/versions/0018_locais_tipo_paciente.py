"""baseline: cria core.locais e core.appointments.local_id/tipo_paciente (Fase 4)

Revision ID: 0018_locais_tipo_paciente
Revises: 0017_glosas
Create Date: 2026-09-04

Mesmo padrão de 0001-0017: DDL revisado manualmente em app/sql/, marcado
aqui como baseline — RLS/DDL sensível a produção continua fora do
autogenerate por princípio (ver os outros arquivos desta pasta).

    psql "$DATABASE_URL" -f app/sql/018_locais_tipo_paciente.sql
    alembic stamp 0018_locais_tipo_paciente

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente (guardado pelo marcador de tabela `locais` em
_POST_UPGRADE_MARKER_TABLE). Ver DECISÃO completa em
app/sql/018_locais_tipo_paciente.sql sobre o que isto fecha (Fase 4 do
plano de adequação ao fluxo real Agendamento -> Atendimento ->
Faturamento).
"""
from collections.abc import Sequence

revision: str = "0018_locais_tipo_paciente"
down_revision: str | None = "0017_glosas"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
