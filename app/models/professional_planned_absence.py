import uuid
from datetime import date, datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProfessionalPlannedAbsence(Base):
    """"Mapa de Dados Insighta" — Domínio Profissional (Onda 1): ausência
    futura planejada (férias, licença) por intervalo de datas — diferente
    de ProfessionalAvailability, que é uma grade semanal RECORRENTE (ver
    DECISÃO completa em 047_professional_planned_absences.sql)."""

    __tablename__ = "professional_planned_absences"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    professional_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.professionals.id"), nullable=False
    )
    start_date: Mapped[date] = mapped_column(nullable=False)
    end_date: Mapped[date] = mapped_column(nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
