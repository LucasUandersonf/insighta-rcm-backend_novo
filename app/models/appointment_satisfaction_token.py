import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AppointmentSatisfactionToken(Base):
    """Token de uso único para o link público de avaliação pós-atendimento
    (ver DECISÃO em 052_appointment_satisfaction.sql). Mesmo padrão de
    PasswordResetToken: SEM RLS de propósito — o paciente acessa o link
    sem estar autenticado, sem contexto de tenant nenhum. Só o hash
    SHA-256 do token é gravado; o valor em texto puro só existe no link
    copiado/enviado pela recepção."""

    __tablename__ = "appointment_satisfaction_tokens"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    appointment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.appointments.id"), nullable=False
    )
    # Denormalizado de propósito — mesmo raciocínio de PasswordResetToken.tenant_id:
    # core.appointments tem RLS, então resolver o dado de verdade exige uma
    # sessão tenant-aware; guardar tenant_id aqui evita mais uma função
    # SECURITY DEFINER só para descobrir "de qual tenant é este appointment_id".
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
