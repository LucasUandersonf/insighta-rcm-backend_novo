import uuid
from datetime import datetime, time

from sqlalchemy import DateTime, ForeignKey, SmallInteger, Time, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProfessionalAvailability(Base):
    """Grade semanal recorrente — não é um calendário de exceções (ver 004_capacity_management.sql)."""

    __tablename__ = "professional_availability"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    professional_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.professionals.id"), nullable=False)
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 0=domingo .. 6=sábado
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
