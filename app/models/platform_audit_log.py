"""
app/models/platform_audit_log.py — ver DECISÃO completa em
app/sql/029_platform_users.sql. Deliberadamente sem `diff` (ao contrário
de core.audit_log de clínica): as ações registradas aqui não têm
antes/depois de estado, só "isto aconteceu, por esta pessoa, nesta hora".
"""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PlatformAuditLog(Base):
    __tablename__ = "platform_audit_log"
    __table_args__ = {"schema": "core"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    platform_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.platform_users.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
