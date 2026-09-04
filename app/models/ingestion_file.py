import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IngestionFile(Base):
    """Rastreia cada arquivo do S3 processado — chave da idempotência do worker."""

    __tablename__ = "ingestion_files"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    s3_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    s3_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    s3_version_id: Mapped[str | None] = mapped_column(String(255))
    file_format: Mapped[str] = mapped_column(String(10), nullable=False)
    # Qual TEMPLATE de integração este arquivo segue — "faturamento"
    # (padrão, retrocompatível com todo arquivo enviado antes desta
    # coluna existir) ou "agenda" (ver app/sql/019_agenda_ingestion.sql).
    # Independente de file_format (csv/xml/json é o CONTAINER; data_type
    # é o CONTEÚDO/esquema de colunas esperado dentro dele).
    data_type: Mapped[str] = mapped_column(String(20), nullable=False, default="faturamento")
    # Nome do arquivo como o usuário o conhece (ex: "faturamento_ago.csv") —
    # só preenchido no caminho HTTP de upload (POST /ingestion/upload, ver
    # app/sql/010_ingestion_original_filename.sql); nulo no caminho SQS,
    # onde a própria s3_key já cumpre esse papel.
    original_filename: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="received")
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    error_row_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
