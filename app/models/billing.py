"""
app/models/billing.py

Model ORM que espelha core.billing (ver 001_init_schema.sql). Note que
NÃO reimplementamos o RLS aqui em Python — o SQLAlchemy nem sabe que RLS
existe. A tabela sempre tem tenant_id como coluna normal; é o Postgres,
por baixo, que filtra as linhas. O ORM só precisa declarar o schema.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Billing(Base):
    __tablename__ = "billing"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    appointment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.appointments.id"), nullable=False)
    insurance_plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.insurance_plans.id"), nullable=False)
    charged_value: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    denial_risk_level: Mapped[str] = mapped_column(String(20), nullable=False, default="low")
    denial_reasons: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    value_saved_by_correction: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    # Valor efetivamente repassado pela operadora — NULL até a liquidação
    # do lote (ver BillingService.settle_billing). Comparado contra
    # ContractItem.agreed_price para detectar underpayment (Divergência de
    # Recebimento) — ver app/repositories/analytics_repository.py.
    received_value: Mapped[float | None] = mapped_column(Numeric(12, 2))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Guia TISS à qual este lançamento pertence (ver app/models/guia.py) —
    # NULLABLE porque todo billing vindo da ingestão em massa hoje não
    # tem noção de guia (o formato de arquivo ainda não carrega isso).
    # Uma guia pode agrupar N linhas de billing (ex.: SADT com vários
    # procedimentos na mesma guia).
    guia_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("core.guias.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
