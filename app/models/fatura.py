"""
app/models/fatura.py

Fatura — Fase 2 do plano de adequação ao fluxo real de mercado
(Agendamento -> Atendimento -> Faturamento). Ver DECISÃO completa em
app/sql/016_lotes_faturas.sql: agrupa um ou mais Lotes fechados para
envio à operadora; a baixa (recebimento) acontece no nível da fatura,
não do lote — é a fatura que a operadora paga.
"""
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

FATURA_STATUSES = ("emitida", "paga", "parcialmente_paga", "cancelada")


class Fatura(Base):
    __tablename__ = "faturas"
    __table_args__ = (
        CheckConstraint(f"status IN {FATURA_STATUSES}", name="faturas_status_check"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    insurance_plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.insurance_plans.id"), nullable=False)
    # Numeração fiscal — mesma multi-seleção "Série-Fatura" que a tela
    # "Faturamento Emitido" de um ERP real mostra (TS, NF, SA, 2, I, NA...).
    # NULLABLE: uma fatura pode existir em rascunho antes de numeração definitiva.
    serie: Mapped[str | None] = mapped_column(String(10))
    numero: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="emitida")
    data_emissao: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Preenchidos só na baixa — mesmo princípio de Billing.received_value/
    # settled_at: NULL = ainda não recebido, nunca 0.
    valor_recebido: Mapped[float | None] = mapped_column(Numeric(12, 2))
    data_recebimento: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
