import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TrackedAlert(Base):
    """Memória contínua dia-a-dia (Roadmap "Rumo à Nota 9", Fase 3) — uma
    linha por situação sinalizada pelos insights, com `resolved_date`
    preenchida quando a situação some da lista de insights ativos. Ver
    DECISÃO completa em app/sql/039_tracked_alerts.sql e
    app/repositories/tracked_alert_repository.py."""

    __tablename__ = "tracked_alerts"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    fact_key: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    first_detected_date: Mapped[date] = mapped_column(Date, nullable=False)
    last_detected_date: Mapped[date] = mapped_column(Date, nullable=False)
    resolved_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
