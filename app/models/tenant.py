import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Tenant(Base):
    """
    Único model do sistema SEM tenant_id — ele É a entidade tenant.
    Não recebe RLS por design (ver comentário no final de 001_init_schema.sql).
    """
    __tablename__ = "tenants"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    trade_name: Mapped[str] = mapped_column(String(255), nullable=False)
    cnpj: Mapped[str] = mapped_column(String(18), nullable=False, unique=True)
    plan_tier: Mapped[str] = mapped_column(String(50), nullable=False, default="starter")
    whatsapp_group_id: Mapped[str | None] = mapped_column(String(100))
    # Segredo do webhook do Meta Ads, por tenant (ver 003_ingestion_tables.sql).
    # Em produção: criptografado em repouso ou referenciado a partir de um
    # Secrets Manager, não guardado em texto puro como neste MVP.
    meta_ads_webhook_secret: Mapped[str | None] = mapped_column(String(255))
    # Meta de faturamento anual, configurada MANUALMENTE pela clínica em
    # "Minha Clínica" — nunca calculada automaticamente pelo sistema
    # (decisão explícita, ver 011_annual_revenue_goal.sql). NULL = meta
    # ainda não configurada, tratado como "não gerar o insight anual",
    # nunca como 0 (mesmo princípio "None sobre zero" do resto do produto).
    annual_revenue_goal: Mapped[float | None] = mapped_column(Numeric(14, 2))
    # Limiares de risco de falta (no-show), configuráveis MANUALMENTE em
    # "Minha Clínica" — mesmo padrão de annual_revenue_goal (NULL = usa o
    # default do módulo, ver DEFAULT_LOW_THRESHOLD/DEFAULT_MEDIUM_THRESHOLD
    # em app/services/no_show_risk_engine.py). Cada especialidade tem um
    # perfil de falta bem diferente (estética vs. saúde mental, por
    # exemplo) — o valor de partida do MVP era um chute razoável, não uma
    # calibração validada; isso deixa cada clínica ajustar o próprio
    # corte ao longo do tempo em vez de esperar o produto "adivinhar".
    no_show_low_threshold: Mapped[float | None] = mapped_column(Numeric(5, 4))
    no_show_medium_threshold: Mapped[float | None] = mapped_column(Numeric(5, 4))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
