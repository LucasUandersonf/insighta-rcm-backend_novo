from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

# Catálogo fixo de planos do MVP — mudar de plano em produção normalmente
# passa por um fluxo comercial (contato com vendas/CS), não um simples
# PATCH self-service; por isso plan_tier não está em TenantUpdateRequest.
# Exposto aqui só para o frontend renderizar "seu plano atual" vs. os
# demais disponíveis, na tela de Gestão de Planos e Assinatura.
AVAILABLE_PLAN_TIERS = ("starter", "professional", "enterprise")


class TenantResponse(BaseModel):
    id: UUID
    legal_name: str
    trade_name: str
    cnpj: str
    plan_tier: str
    is_active: bool
    created_at: datetime
    # Meta manual de faturamento anual (ver DECISÃO em app/models/tenant.py
    # e 011_annual_revenue_goal.sql) — null quando a clínica ainda não
    # configurou uma meta; alimenta o insight de desempenho anual da Sala
    # de Comando (app/services/smart_insights_engine.py).
    annual_revenue_goal: float | None = None
    # Limiares de risco de falta (ver DECISÃO em app/models/tenant.py e
    # 020_no_show_thresholds.sql) — null quando a clínica não configurou
    # (o motor usa o default do módulo, DEFAULT_LOW_THRESHOLD/
    # DEFAULT_MEDIUM_THRESHOLD em app/services/no_show_risk_engine.py).
    # Frações 0-1 (ex: 0.10 = 10%), não percentuais.
    no_show_low_threshold: float | None = None
    no_show_medium_threshold: float | None = None

    model_config = {"from_attributes": True}


class TenantUpdateRequest(BaseModel):
    """Dados cadastrais que o próprio owner pode manter — nunca plan_tier
    nem cnpj (mudança de CNPJ é operação de suporte/KYC, não self-service).

    Mesma convenção de todo campo opcional aqui: None = "não alterar este
    campo neste PATCH", não "limpar o valor" — consistente com legal_name/
    trade_name já existentes. Enviar um novo valor sempre precisa ser >0
    (uma meta de R$0 não tem sentido de negócio; "sem meta" é omitir o
    campo, não zerá-lo).

    `no_show_low_threshold`/`no_show_medium_threshold` são frações 0-1
    (não percentuais — 0.10 = 10%), validadas em (0, 1) pelo Field; a
    consistência CRUZADA (low < medium) depende do valor JÁ SALVO no
    outro campo quando só um dos dois é enviado num PATCH, então é
    validada no service (TenantService.update_own_tenant), não aqui."""

    legal_name: str | None = None
    trade_name: str | None = None
    annual_revenue_goal: float | None = Field(default=None, gt=0)
    no_show_low_threshold: float | None = Field(default=None, gt=0, lt=1)
    no_show_medium_threshold: float | None = Field(default=None, gt=0, lt=1)


class NoShowThresholdSuggestionResponse(BaseModel):
    """GET /tenant/no-show-thresholds/suggested — ver DECISÃO completa em
    no_show_risk_engine.suggest_thresholds. Campos None quando a clínica
    ainda não tem histórico suficiente (menos de MIN_PATIENTS_FOR_SUGGESTION
    pacientes qualificados) — nunca um valor calculado sobre amostra
    pequena demais para significar algo real."""

    low_threshold: float | None
    medium_threshold: float | None
    sample_size: int
