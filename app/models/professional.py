import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# "Mapa de Dados Insighta" — Domínio Profissional (Onda 2): arranjos de
# contratação mais comuns entre profissionais de saúde no Brasil — ver
# DECISÃO completa em 051_professional_contract_commission.sql.
CONTRACT_TYPE_VALUES = ("clt", "pj", "autonomo", "cooperado")


class Professional(Base):
    __tablename__ = "professionals"
    __table_args__ = (
        CheckConstraint(
            f"contract_type IS NULL OR contract_type IN {CONTRACT_TYPE_VALUES}",
            name="professionals_contract_type_check",
        ),
        CheckConstraint(
            "commission_rate IS NULL OR (commission_rate >= 0 AND commission_rate <= 100)",
            name="professionals_commission_rate_check",
        ),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    professional_registry: Mapped[str | None] = mapped_column(String(30))
    specialty: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # "Mapa de Dados Insighta" — Domínio Profissional (Onda 2). Ver
    # CONTRACT_TYPE_VALUES acima e DECISÃO completa em
    # 051_professional_contract_commission.sql.
    contract_type: Mapped[str | None] = mapped_column(String(20))
    commission_rate: Mapped[float | None] = mapped_column(Numeric(5, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
