import uuid
from datetime import date, datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class HealthScoreSnapshot(Base):
    """Fotografia mensal da Nota de Saúde Financeira de um tenant — ver
    DECISÃO completa em app/sql/034_health_score_snapshots.sql. Usada só
    para calcular tendência (AnalyticsService.get_health_score), nunca
    exibida linha a linha."""

    __tablename__ = "health_score_snapshots"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    snapshot_month: Mapped[date] = mapped_column(nullable=False)
    # NULLABLE — mesmo critério de HealthScoreResponse.score: None quando
    # a amostra do período era insuficiente em TODOS os componentes.
    score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
