"""
app/models/glosa.py

Glosa REAL — Fase 3 do plano de adequação ao fluxo real de mercado
(Agendamento -> Atendimento -> Faturamento). Ver DECISÃO completa em
app/sql/017_glosas.sql: registra o FATO de que a operadora negou/
reduziu um valor, independente de a clínica decidir recorrer (isso é
DenialAppeal, entidade separada e pré-existente — ver
app/models/denial_appeal.py).
"""
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Glosa(Base):
    __tablename__ = "glosas"
    __table_args__ = (
        CheckConstraint("valor_glosado > 0", name="glosas_valor_check"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    billing_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.billing.id"), nullable=False)
    # Código do motivo de glosa (Tabela 27 do padrão TISS/ANS) —
    # NULLABLE porque nem sempre a operadora devolve o código
    # estruturado (às vezes só um demonstrativo em texto livre).
    codigo_motivo: Mapped[str | None] = mapped_column(String(10))
    descricao_motivo: Mapped[str | None] = mapped_column(Text)
    valor_glosado: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    data_recebimento: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
