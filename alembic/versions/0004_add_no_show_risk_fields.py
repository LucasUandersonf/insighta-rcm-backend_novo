"""adiciona no_show_risk_level e no_show_risk_score em appointments

Revision ID: 0004_no_show_risk
Revises: 0003_capacity_baseline
Create Date: 2026-08-27

DIFERENTE das revisions anteriores (0001-0003): esta é uma migration
"de verdade", não um stamp de baseline. As colunas adicionadas aqui não
envolvem RLS nem nenhum objeto sensível de segurança — só duas colunas
nullable numa tabela que já existe — então seguem o fluxo normal do
Alembic (o que `alembic revision --autogenerate` geraria comparando
app/models/appointment.py com o estado atual do banco), em vez do SQL
manual revisado usado para 001-004 em app/sql/.

Aplicar:
    alembic upgrade head
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_no_show_risk"
down_revision: str | None = "0003_capacity_baseline"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "appointments",
        sa.Column("no_show_risk_level", sa.String(length=20), nullable=True),
        schema="core",
    )
    op.add_column(
        "appointments",
        sa.Column("no_show_risk_score", sa.Numeric(precision=5, scale=4), nullable=True),
        schema="core",
    )
    op.create_check_constraint(
        "ck_appointments_no_show_risk_level",
        "appointments",
        "no_show_risk_level IN ('indeterminado', 'baixo', 'medio', 'alto')",
        schema="core",
    )


def downgrade() -> None:
    op.drop_constraint("ck_appointments_no_show_risk_level", "appointments", schema="core", type_="check")
    op.drop_column("appointments", "no_show_risk_score", schema="core")
    op.drop_column("appointments", "no_show_risk_level", schema="core")
