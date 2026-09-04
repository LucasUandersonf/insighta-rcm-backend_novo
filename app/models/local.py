"""
app/models/local.py

Local de Atendimento (Unidade/Setor) — Fase 4 do plano de adequação ao
fluxo real de mercado. Ver DECISÃO completa em
app/sql/018_locais_tipo_paciente.sql: catálogo próprio por tenant,
mesmo padrão de desativação (não exclusão) já usado em
Professional/InsuranceCompany/InsurancePlan.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Local(Base):
    __tablename__ = "locais"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    nome: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
