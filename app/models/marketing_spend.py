import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MarketingSpend(Base):
    __tablename__ = "marketing_spend"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(100), nullable=False)
    campaign_name: Mapped[str | None] = mapped_column(String(255))
    spend_date: Mapped[date] = mapped_column(nullable=False)
    amount_spent: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    impressions: Mapped[int | None] = mapped_column(BigInteger)
    clicks: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
