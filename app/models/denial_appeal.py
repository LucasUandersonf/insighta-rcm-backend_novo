"""
app/models/denial_appeal.py

Ver DECISÃO completa em app/sql/008_denial_appeals.sql — esta é a
NEGATIVA FORMAL pós-envio (glosa administrativa/médica, com prazo de
contestação), diferente do denial_risk_engine.py (glosa técnica
pré-envio, sem "processo" nenhum).
"""
import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

APPEAL_TYPES = ("tecnica", "administrativa", "medica")
APPEAL_STATUSES = ("aberto", "protocolado", "deferido", "indeferido", "nip_aberta")


class DenialAppeal(Base):
    __tablename__ = "denial_appeals"
    __table_args__ = (
        CheckConstraint("appeal_type IN ('tecnica', 'administrativa', 'medica')", name="ck_denial_appeals_type"),
        CheckConstraint(
            "status IN ('aberto', 'protocolado', 'deferido', 'indeferido', 'nip_aberta')",
            name="ck_denial_appeals_status",
        ),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    billing_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.billing.id"), nullable=False)

    appeal_type: Mapped[str] = mapped_column(String(20), nullable=False)
    operator_denial_reason: Mapped[str | None] = mapped_column(Text)
    denied_at: Mapped[date] = mapped_column(Date, nullable=False)
    deadline_at: Mapped[date] = mapped_column(Date, nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="aberto")
    filed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_notes: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("core.users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DenialAppealAttachment(Base):
    __tablename__ = "denial_appeal_attachments"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    appeal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.denial_appeals.id"), nullable=False)
    s3_key: Mapped[str] = mapped_column(String(512), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("core.users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
