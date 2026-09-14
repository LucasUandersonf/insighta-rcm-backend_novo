"""
app/schemas/cost_entry.py

Plano Diretor Insighta, épico F3.1 ("Módulo de custos e margem real")
— ver DECISÃO completa em app/sql/039_cost_entries.sql.
"""
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class CostEntryCreateRequest(BaseModel):
    category: str = Field(pattern="^(folha_fixa|comissao_repasse|aluguel|insumo|outros)$")
    description: str | None = None
    amount: float = Field(gt=0)
    period_month: date
    professional_id: UUID | None = None

    @field_validator("period_month")
    @classmethod
    def normalize_to_first_day(cls, v: date) -> date:
        """Sempre o dia 1 do mês — mesma normalização de snapshot_month
        em health_score_snapshots (ver DECISÃO no SQL): o usuário pode
        mandar qualquer dia do mês, a granularidade real é mensal."""
        return v.replace(day=1)


class CostEntryResponse(BaseModel):
    id: UUID
    category: str
    description: str | None
    amount: float
    period_month: date
    professional_id: UUID | None
    created_by: UUID
    created_at: datetime

    model_config = {"from_attributes": True}
