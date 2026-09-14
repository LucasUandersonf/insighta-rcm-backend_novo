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
    # Épico F2.1 do Plano Diretor ("Calibração por especialidade/porte") —
    # ver DECISÃO completa em app/models/tenant.py e
    # 040_tenant_calibration_fields.sql. `specialty` é texto livre curto,
    # descritivo; os limiares seguem a MESMA convenção de
    # no_show_low_threshold acima (null = usa o default do módulo).
    specialty: str | None = None
    denial_risk_warning_threshold: float | None = None
    denial_risk_critical_threshold: float | None = None
    health_score_denial_ceiling: float | None = None
    health_score_no_show_ceiling: float | None = None

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
    validada no service (TenantService.update_own_tenant), não aqui.

    Épico F2.1 do Plano Diretor — MESMA convenção acima para os novos
    campos: `denial_risk_*_threshold` são PERCENTUAIS (0-100, mesma
    escala de denial_risk_pct), `health_score_*_ceiling` são frações 0-1
    (mesma escala dos ceilings de health_score_engine.py); consistência
    cruzada (warning < critical) também validada no service, não aqui.
    `specialty` é texto livre curto, sem validação de vocabulário fechado
    (mesma decisão de canal_agendamento/motivo_cancelamento — ver
    RawAppointmentRow)."""

    legal_name: str | None = None
    trade_name: str | None = None
    annual_revenue_goal: float | None = Field(default=None, gt=0)
    no_show_low_threshold: float | None = Field(default=None, gt=0, lt=1)
    no_show_medium_threshold: float | None = Field(default=None, gt=0, lt=1)
    specialty: str | None = Field(default=None, max_length=100)
    denial_risk_warning_threshold: float | None = Field(default=None, gt=0, lt=100)
    denial_risk_critical_threshold: float | None = Field(default=None, gt=0, lt=100)
    health_score_denial_ceiling: float | None = Field(default=None, gt=0, lt=1)
    health_score_no_show_ceiling: float | None = Field(default=None, gt=0, lt=1)


class NoShowThresholdSuggestionResponse(BaseModel):
    """GET /tenant/no-show-thresholds/suggested — ver DECISÃO completa em
    no_show_risk_engine.suggest_thresholds. Campos None quando a clínica
    ainda não tem histórico suficiente (menos de MIN_PATIENTS_FOR_SUGGESTION
    pacientes qualificados) — nunca um valor calculado sobre amostra
    pequena demais para significar algo real."""

    low_threshold: float | None
    medium_threshold: float | None
    sample_size: int


class DenialRiskThresholdSuggestionResponse(BaseModel):
    """GET /tenant/denial-risk-thresholds/suggested — ver DECISÃO completa
    em smart_insights_engine.suggest_denial_risk_thresholds. Campos None
    sem meses de histórico suficientes (menos de
    MIN_MONTHS_FOR_DENIAL_RISK_SUGGESTION)."""

    warning_threshold: float | None
    critical_threshold: float | None
    sample_size: int


class HealthScoreCeilingSuggestionResponse(BaseModel):
    """GET /tenant/health-score-ceilings/suggested — ver DECISÃO completa
    em health_score_engine.suggest_denial_rate_ceiling/
    suggest_no_show_rate_ceiling. Cada teto é sugerido a partir de uma
    amostra PRÓPRIA (meses com glosa calculável vs. meses com atendimento
    concluído/faltado) — por isso cada um tem seu próprio sample_size, os
    dois podem divergir. Campo None quando a amostra daquele componente
    específico é insuficiente."""

    denial_ceiling: float | None
    denial_ceiling_sample_size: int
    no_show_ceiling: float | None
    no_show_ceiling_sample_size: int


class AnnualGoalSuggestionResponse(BaseModel):
    """GET /tenant/annual-goal/suggested — Épico F3.3 do Plano Diretor
    ("Metas e cenários orientados a dados"): "meta anual sugerida
    (crescimento histórico + percentil de rede)". Ver DECISÃO completa
    em app/sql/041_network_revenue_growth_benchmark.sql sobre por que
    são DUAS sugestões independentes, nunca uma média escondida.

    - `own_trend_suggested_goal` = `trailing_12_months_total` projetado
      pelo SEU PRÓPRIO crescimento (últimos 12 meses vs os 12 anteriores).
      None sem faturamento nos 12 meses anteriores (base indefinida).
    - `network_pace_suggested_goal` = o MESMO faturamento seu, projetado
      pelo ritmo (mediana) de crescimento de outras clínicas ativas.
      None abaixo do cohort mínimo de clínicas comparáveis.

    Nenhuma sugestão é aplicada sozinha — o formulário só preenche o
    campo quando o usuário clica."""

    trailing_12_months_total: float
    own_growth_rate: float | None
    own_trend_suggested_goal: float | None
    network_growth_median: float | None
    network_pace_suggested_goal: float | None
    network_cohort_size: int
