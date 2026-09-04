"""
app/schemas/glosa.py

Glosa REAL — ver DECISÃO completa em app/models/glosa.py e
app/sql/017_glosas.sql.
"""
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class GlosaCreateRequest(BaseModel):
    billing_id: UUID
    codigo_motivo: str | None = None
    descricao_motivo: str | None = None
    valor_glosado: float = Field(gt=0)
    # Opcional: quando a operadora não informa a data exata do
    # demonstrativo, assume-se "agora" (o registro está sendo feito
    # no momento em que a informação chegou).
    data_recebimento: datetime | None = None


class GlosaResponse(BaseModel):
    id: UUID
    billing_id: UUID
    codigo_motivo: str | None
    descricao_motivo: str | None
    valor_glosado: float
    data_recebimento: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class RiskLevelReconciliation(BaseModel):
    """Uma linha da tabela de conciliação, por denial_risk_level
    PREVISTO pelo motor (ver denial_risk_engine.py)."""

    level: str
    billing_count: int
    glosado_count: int
    valor_glosado_total: float


class GlosaReconciliationResponse(BaseModel):
    """
    Previsto x Realizado — a métrica que faltava para provar que o
    motor de risco de glosa (denial_risk_engine.py) funciona de
    verdade, não só "parece funcionar". Vocabulário de classificador
    binário (a previsão é "vai ter glosa" sim/não — medium e high
    juntos contam como "previu risco"):

    - true_positive: previsto medium/high E teve glosa real -> acertou.
    - false_positive: previsto medium/high E NÃO teve glosa -> alarme
      falso (retém faturamento à toa, sem necessidade real).
    - false_negative: previsto low E teve glosa real -> PONTO CEGO do
      motor — o caso mais caro de errar, porque ninguém revisou antes
      de enviar.
    - true_negative: previsto low E não teve glosa -> acertou (a
      maioria dos casos, normalmente).
    """

    period_start: date
    period_end: date
    by_risk_level: list[RiskLevelReconciliation]
    true_positive_count: int
    false_positive_count: int
    false_negative_count: int
    true_negative_count: int
    # None quando o denominador é zero (sem base para significar nada —
    # mesmo princípio de _delta_pct/_denial_risk_pct em analytics_service.py).
    precision_pct: float | None
    recall_pct: float | None
    valor_glosado_previsto: float
    valor_glosado_nao_previsto: float
