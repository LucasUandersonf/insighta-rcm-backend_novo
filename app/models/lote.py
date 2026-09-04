"""
app/models/lote.py

Lote — Fase 2 do plano de adequação ao fluxo real de mercado
(Agendamento -> Atendimento -> Faturamento). Ver DECISÃO completa em
app/sql/016_lotes_faturas.sql: agrupa Guias do MESMO convênio + MESMO
tipo (confirmado de forma idêntica em 3 ERPs do mercado — Moderna,
Feegow, iClinic), com ciclo de vida aberto -> fechado -> faturado.
"""
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.guia import GUIA_TIPOS

LOTE_STATUSES = ("aberto", "fechado", "faturado")


class Lote(Base):
    __tablename__ = "lotes"
    __table_args__ = (
        CheckConstraint(f"tipo IN {GUIA_TIPOS}", name="lotes_tipo_check"),
        CheckConstraint(f"status IN {LOTE_STATUSES}", name="lotes_status_check"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    insurance_plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.insurance_plans.id"), nullable=False)
    tipo: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="aberto")
    fatura_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("core.faturas.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
