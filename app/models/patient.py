import uuid
from datetime import date, datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Patient(Base):
    __tablename__ = "patients"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    cpf: Mapped[str | None] = mapped_column(String(14))
    birth_date: Mapped[date | None]
    # Rastreamento de origem de marketing — alimenta o cálculo de ROI (Tela C)
    acquisition_source: Mapped[str | None] = mapped_column(String(50))
    acquisition_campaign_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Direito de eliminação do titular (LGPD art. 18, VI) — ver DECISÃO
    # completa em app/sql/022_patient_lgpd_erasure.sql e
    # app/services/patient_service.py (anonymize_patient). NULL = dado
    # pessoal intacto; preenchido = full_name/cpf/birth_date/acquisition_*
    # já foram substituídos por um placeholder (nunca DELETE físico).
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
