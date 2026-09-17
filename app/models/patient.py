import uuid
from datetime import date, datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# "Mapa de Dados Insighta" — Domínio Paciente: janela de horário
# preferida (usada no preenchimento assistido de horário ocioso).
PREFERRED_TIME_WINDOW_VALUES = ("manha", "tarde", "noite")


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
    # "Mapa de Dados Insighta" — Domínio Paciente (Onda 1): o paciente
    # RELACIONAL, não só transacional. Ver DECISÃO completa em
    # 045_patient_relationship_fields.sql (validação de tenant de
    # referred_by_patient_id fica no service, não na FK; NULL/False
    # nunca inventados em communication_consent).
    referred_by_patient_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("core.patients.id"))
    communication_consent: Mapped[bool | None]
    preferred_time_window: Mapped[str | None] = mapped_column(String(10))
    zip_code: Mapped[str | None] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Direito de eliminação do titular (LGPD art. 18, VI) — ver DECISÃO
    # completa em app/sql/022_patient_lgpd_erasure.sql e
    # app/services/patient_service.py (anonymize_patient). NULL = dado
    # pessoal intacto; preenchido = full_name/cpf/birth_date/acquisition_*
    # já foram substituídos por um placeholder (nunca DELETE físico).
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
