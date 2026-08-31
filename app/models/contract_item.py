import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ContractItem(Base):
    """Uma linha da tabela de preços de um contrato: um código TUSS e o
    valor acordado para ele. É o que o Parser Inteligente (IA) extrai do
    PDF e o faturista homologa (ver contract_extraction_service.py) —
    nunca gravado direto sem revisão humana (ver ContractService.homologate)."""

    __tablename__ = "contract_items"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.contracts.id"), nullable=False)
    tuss_code: Mapped[str] = mapped_column(String(20), nullable=False)
    procedure_name: Mapped[str | None] = mapped_column(String(255))
    agreed_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
