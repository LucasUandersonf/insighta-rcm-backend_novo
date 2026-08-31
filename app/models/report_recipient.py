"""
app/models/report_recipient.py

Ver DECISÃO completa em app/sql/009_report_recipients.sql — cadastro de
múltiplos destinatários (WhatsApp/email) de relatório por tenant,
opcionalmente restritos a um subconjunto de tipos de relatório.
"""
import uuid
from datetime import datetime

from sqlalchemy import ARRAY, Boolean, CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReportRecipient(Base):
    __tablename__ = "report_recipients"
    __table_args__ = (
        CheckConstraint(
            "phone_whatsapp IS NOT NULL OR email IS NOT NULL",
            name="ck_report_recipients_has_contact",
        ),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone_whatsapp: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)

    # '{}' (vazio) = recebe todos os tipos de relatório — ver DECISÃO em
    # app/sql/009_report_recipients.sql.
    report_types: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # onupdate no nível do ORM (não um trigger de banco): não há
    # trigger de `updated_at` em app/sql/009_report_recipients.sql, então
    # todo UPDATE feito via SQLAlchemy (o único caminho de escrita deste
    # model) recebe o timestamp automaticamente; uma escrita SQL manual
    # fora do ORM não atualizaria esta coluna sozinha.
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
