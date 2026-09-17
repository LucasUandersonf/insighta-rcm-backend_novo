import uuid
from datetime import date, datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

INSIGHT_OUTCOME_STATUSES = ("pendente", "em_andamento", "resolvido", "ignorado")


class InsightOutcome(Base):
    """Ciclo de vida de um insight que o gestor decidiu acompanhar
    (Plano Diretor, épico F1.2) e/ou delegar (épico F1.3) — ver DECISÃO
    completa em app/sql/038_insight_outcomes.sql. `insight_key` é um
    slug (não um FK) porque generate_insights() nunca persiste
    instâncias — ver DECISÃO no motor puro (smart_insights_engine.py)."""

    __tablename__ = "insight_outcomes"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    insight_key: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    financial_impact_snapshot: Mapped[float | None] = mapped_column(Numeric(14, 2))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pendente")
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("core.users.id", ondelete="SET NULL"))
    due_date: Mapped[date | None] = mapped_column()
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_metric_value: Mapped[float | None] = mapped_column(Numeric(14, 2))
    reevaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
