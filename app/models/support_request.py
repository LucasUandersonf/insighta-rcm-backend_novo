"""
app/models/support_request.py — Central de Ajuda, "enviar uma pergunta"
sem sair do sistema. Ver DECISÃO completa em
app/sql/023_announcements_and_support.sql.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

SUPPORT_REQUEST_STATUSES = ("aberto", "respondido")


class SupportRequest(Base):
    __tablename__ = "support_requests"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.users.id"), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="aberto")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
