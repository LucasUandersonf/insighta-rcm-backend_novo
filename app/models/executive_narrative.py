import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExecutiveNarrative(Base):
    """Resumo executivo narrado por IA, uma linha por (tenant_id,
    digest_date) — ver DECISÃO completa em
    app/sql/038_executive_narratives.sql e
    app/services/executive_narrative_service.py. Cache diário: nunca
    regenerado a cada abertura da Sala de Comando."""

    __tablename__ = "executive_narratives"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    digest_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Janela de dados que alimentou a narrativa (sempre os últimos 7 dias
    # fechados — ver DECISÃO no service) — guardado só para transparência
    # na UI, nunca recalculado a partir daqui.
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    narrative_text: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
