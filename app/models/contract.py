import uuid
from datetime import date, datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# rascunho: PDF subiu, extração da IA ainda não rodou (ou não foi salva).
# em_revisao: IA já extraiu os itens; aguardando homologação humana.
# homologado: humano confirmou — só este estado alimenta o motor de
# glosa/analytics (ver ContractItemRepository.find_agreed_price).
CONTRACT_STATUSES = ("rascunho", "em_revisao", "homologado")


class Contract(Base):
    """Cabeçalho do contrato de repasse: UMA vigência, UM PDF de origem,
    N itens de preço (ver ContractItem). Ver DECISÃO em
    app/sql/007_contract_intelligence.sql sobre por que isso deixou de
    carregar procedure_code/agreed_value diretamente na linha."""

    __tablename__ = "contracts"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    insurance_plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.insurance_plans.id"), nullable=False)
    valid_from: Mapped[date] = mapped_column(nullable=False)
    valid_until: Mapped[date | None]
    pdf_s3_key: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="homologado")
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    homologated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("core.users.id"))
    homologated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
