"""
app/models/ingestion_column_alias.py

Ver DECISÃO completa em app/sql/021_ingestion_column_aliases.sql —
mapeador automático de coluna: "o cabeçalho X do arquivo deste tenant
corresponde ao campo canônico Y", aprendido uma vez por tenant+template.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IngestionColumnAlias(Base):
    __tablename__ = "ingestion_column_aliases"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    data_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_header: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_field: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
