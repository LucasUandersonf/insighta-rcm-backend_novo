import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InsuranceCompany(Base):
    """Operadora de convênio (Amil, Bradesco Saúde, Unimed...). Um plano
    (InsurancePlan) pertence a uma operadora; uma operadora tem N planos."""

    __tablename__ = "insurance_companies"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ans_registry: Mapped[str | None] = mapped_column(String(20))
    # NULLABLE de propósito — prazo de recurso é contratual (varia por
    # operadora, não é lei federal única), ver DECISÃO em
    # app/sql/008_denial_appeals.sql. NULL usa o fallback genérico
    # settings.DEFAULT_APPEAL_DEADLINE_DAYS.
    default_appeal_deadline_days: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
