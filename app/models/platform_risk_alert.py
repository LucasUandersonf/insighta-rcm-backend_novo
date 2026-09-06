"""
app/models/platform_risk_alert.py — Ver DECISÃO completa em
app/sql/027_platform_risk_alerts.sql: mesma exceção de
platform_announcements (nunca acessada por uma sessão tenant-aware), mas
diferente dela POR TER tenant_id — aqui é uma referência de bookkeeping
("qual clínica está em episódio de risco"), não dado isolável por RLS.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PlatformRiskAlert(Base):
    __tablename__ = "platform_risk_alerts"
    __table_args__ = {"schema": "core"}

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.tenants.id"), primary_key=True
    )
    first_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_alert_sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
