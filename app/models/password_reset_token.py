import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PasswordResetToken(Base):
    """Token de uso único para "esqueci minha senha" (self-service).

    Sem tenant_id / sem RLS de propósito — ver DECISÃO em
    app/sql/012_password_reset.sql (mesma razão de core.tenants: o fluxo
    acontece ANTES de existir contexto de tenant). Só o HASH SHA-256 do
    token é gravado (`token_hash`); o valor em texto puro só existe no
    e-mail enviado ao usuário e nunca toca o banco."""

    __tablename__ = "password_reset_tokens"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.users.id"), nullable=False)
    # Denormalizado de propósito — ver DECISÃO em app/sql/012_password_reset.sql:
    # evita precisar de outra função SECURITY DEFINER só para descobrir o
    # tenant do usuário no momento de confirmar o reset (core.users tem RLS).
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
