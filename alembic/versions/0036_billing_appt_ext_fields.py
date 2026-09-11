"""baseline: campos novos do Dicionário de Dados (Billing/Appointment)

Revision ID: 0036_billing_appt_ext_fields
Revises: 0035_glosa_ingestion
Create Date: 2026-09-11

Mesmo padrão de 0001-0035: DDL revisado manualmente em app/sql/, marcado
aqui como baseline.

DECISÃO — revision id abreviado ("appt_ext_fields", não
"appointment_extended_fields")
-------------------------------------------------------------------
`core.alembic_version.version_num` é VARCHAR(32) (padrão do Alembic) —
"0036_billing_appointment_extended_fields" tem 40 caracteres e
ultrapassa esse limite, o que só é descoberto em runtime (erro
StringDataRightTruncationError no primeiro `alembic upgrade head` contra
banco zerado, ver app/scripts/verify_bootstrap_from_zero.py). O nome do
ARQUIVO SQL (app/sql/036_billing_appointment_extended_fields.sql) não
tem essa restrição — só o revision id do Alembic precisa ficar curto.

    psql "$DATABASE_URL" -f app/sql/036_billing_appointment_extended_fields.sql
    alembic stamp 0036_billing_appt_ext_fields

Em produção/Railway, app/scripts/bootstrap_db.py já aplica este arquivo
automaticamente (ADD COLUMN IF NOT EXISTS + DROP/ADD CONSTRAINT —
auto-idempotente, roda em todo deploy, sem entrar na
_POST_UPGRADE_MARKER_TABLE). Ver DECISÃO completa em
app/sql/036_billing_appointment_extended_fields.sql.
"""
from collections.abc import Sequence

revision: str = "0036_billing_appt_ext_fields"
down_revision: str | None = "0035_glosa_ingestion"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
