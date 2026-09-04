import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InsurancePlan(Base):
    __tablename__ = "insurance_plans"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    # NULLABLE de propósito — ver DECISÃO em app/sql/007_contract_intelligence.sql:
    # planos cadastrados antes desta feature continuam válidos sem operadora.
    insurance_company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.insurance_companies.id")
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(255), nullable=False)
    ans_registry: Mapped[str | None] = mapped_column(String(20))
    # Desativação, não exclusão — Contract/Appointment/Billing e
    # insurance_plan_aliases referenciam este id, então apagar de verdade
    # quebraria essas FKs (ou exigiria cascata destruindo histórico
    # financeiro real). Some dos seletores de cadastro novo
    # (InsurancePlanRepository.list_active) sem afetar o motor de
    # resolução de ingestão (resolve() continua casando por
    # normalized_key/alias independente disto — um arquivo do ERP que
    # ainda cita o plano antigo continua reconciliando, mesmo desativado).
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
