"""
app/schemas/marketing_spend.py

Achado do Dossiê Insighta RCM — Onda 2 do Plano de Ação: `core.marketing_spend`
(ver app/sql/001_init_schema.sql) tinha query de leitura pronta e
testada (ReportingRepository.marketing_spend_total/revenue_from_campaign_patients),
mas nenhum código do sistema escrevia uma linha nela — CAC/ROI de
marketing sempre mostrou receita sem nunca mostrar o gasto real.
Mesmo padrão de entrada manual de app/schemas/cost_entry.py (F3.1).
"""
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field

# Mesmo vocabulário fechado do CHECK constraint em core.marketing_spend
# (ver 001_init_schema.sql) — a tabela nasceu pensada pra webhook/ETL do
# Meta/Google Ads; a entrada manual aqui respeita o MESMO vocabulário
# (Instagram/Facebook entram como "meta_ads", é o mesmo anunciante).
SOURCE_VALUES = ("meta_ads", "google_ads")


class MarketingSpendCreateRequest(BaseModel):
    source: str = Field(pattern="^(meta_ads|google_ads)$")
    campaign_id: str = Field(min_length=1)
    campaign_name: str | None = None
    spend_date: date
    amount_spent: float = Field(gt=0)
    impressions: int | None = Field(default=None, ge=0)
    clicks: int | None = Field(default=None, ge=0)


class MarketingSpendResponse(BaseModel):
    id: UUID
    source: str
    campaign_id: str
    campaign_name: str | None
    spend_date: date
    amount_spent: float
    impressions: int | None
    clicks: int | None
    created_at: datetime

    model_config = {"from_attributes": True}
