"""
app/models/guia.py

Guia (TISS) — Fase 1 do plano de adequação ao fluxo real de mercado
(Agendamento -> Atendimento -> Faturamento). Ver DECISÃO completa em
app/sql/015_billing_guia.sql: 1 Guia pode agrupar N linhas de
Billing (ver Billing.guia_id) — uma guia SP/SADT real frequentemente
tem vários procedimentos/itens.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Os 4 tipos oficiais do padrão TISS/ANS — confirmados tanto na tela
# "Gera Arquivo - TISS" de um ERP real (checkboxes Consulta/Honorários/
# SADT/Resumo Internação) quanto na documentação pública da ANS
# (Padrão TISS — Componente Organizacional). Sem variação por operadora.
GUIA_TIPOS = ("consulta", "sadt", "resumo_internacao", "honorario")


class Guia(Base):
    __tablename__ = "guias"
    __table_args__ = (
        CheckConstraint(f"tipo IN {GUIA_TIPOS}", name="guias_tipo_check"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    insurance_plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.insurance_plans.id"), nullable=False)
    tipo: Mapped[str] = mapped_column(String(30), nullable=False)
    # Número da guia — atribuído pelo prestador (rascunho) ou pela
    # operadora (após autorização). NULLABLE: uma guia pode existir
    # antes de ter número definitivo.
    numero: Mapped[str | None] = mapped_column(String(50))
    # Senha de autorização + validade — só existe quando o procedimento
    # exigiu autorização prévia (nem todo atendimento exige).
    senha: Mapped[str | None] = mapped_column(String(50))
    senha_validade: Mapped[date | None] = mapped_column(Date)
    # Código da tabela de procedimento (padrão TISS: 18=CBHPM,
    # 19/20=tabela própria, 22=TUSS) — NULLABLE por ora.
    tabela_procedimento: Mapped[str | None] = mapped_column(String(5))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
