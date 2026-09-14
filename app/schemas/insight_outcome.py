"""
app/schemas/insight_outcome.py

Plano Diretor Insighta — épicos F1.2 (ciclo fechado de insight) + F1.3
(atribuição/workflow). Ver DECISÃO completa em
app/sql/038_insight_outcomes.sql.
"""
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class InsightOutcomeCreateRequest(BaseModel):
    """POST /insight-outcomes — "marcar como resolvido"/"atribuir" um
    item da fila (GET /priority-queue) ou do feed normal
    (GET /smart-insights). O frontend manda o SNAPSHOT do item (título,
    mensagem, impacto) porque generate_insights() nunca persiste
    instâncias — ver DECISÃO no motor puro."""

    source: str = Field(pattern="^(insight|raiox)$")
    category: str
    severity: str
    title: str
    message: str
    financial_impact: float | None = None
    assigned_to: UUID | None = None
    due_date: date | None = None


class InsightOutcomeUpdateRequest(BaseModel):
    """PATCH /insight-outcomes/{id} — todos os campos opcionais, só
    muda o que vier preenchido. `status` é a única coisa que o próprio
    ATRIBUÍDO (não só quem tem _CAN_WRITE) pode alterar em si mesmo —
    ver RBAC em app/api/v1/endpoints/insight_outcomes.py."""

    status: str | None = Field(default=None, pattern="^(pendente|em_andamento|resolvido|ignorado)$")
    assigned_to: UUID | None = None
    due_date: date | None = None
    resolution_note: str | None = None


class InsightOutcomeResponse(BaseModel):
    id: UUID
    insight_key: str
    source: str
    category: str
    severity: str
    title: str
    message: str
    financial_impact_snapshot: float | None
    status: str
    assigned_to: UUID | None
    due_date: date | None
    resolution_note: str | None
    resolved_at: datetime | None
    resolved_metric_value: float | None
    reevaluated_at: datetime | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class InsightOutcomesRealizedSummary(BaseModel):
    """Painel "Insights que valeram a pena" — mesma lógica do card de
    valor protegido pelo motor anti-glosa, mas pra QUALQUER categoria
    que passou pelo ciclo fechado (F1.2), não só glosa. Só conta
    outcomes já REAVALIADOS (reevaluated_at not null) — nunca soma uma
    promessa ainda não conferida."""

    total_resolved_and_reevaluated: int
    # Soma de delta_realized (financial_impact_snapshot - resolved_metric_value)
    # entre os outcomes reavaliados — positivo = o problema efetivamente
    # diminuiu depois de marcado resolvido.
    total_delta_realized: float
    items: list[InsightOutcomeResponse]
