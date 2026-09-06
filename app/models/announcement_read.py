"""
app/models/announcement_read.py — "quem já viu qual novidade" (por
usuário). Ver DECISÃO completa em app/sql/023_announcements_and_support.sql.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AnnouncementRead(Base):
    __tablename__ = "announcement_reads"
    __table_args__ = {"schema": "core"}

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.users.id"), primary_key=True
    )
    announcement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.platform_announcements.id"), primary_key=True
    )
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
