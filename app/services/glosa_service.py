"""
app/services/glosa_service.py

Glosa REAL — ver DECISÃO completa em app/models/glosa.py e
app/schemas/glosa.py (a lógica de Previsto x Realizado mora aqui,
`reconciliation_by_risk_level` do repositório só traz os números
crus agregados por nível).
"""
import uuid
from datetime import date, datetime, timezone

from fastapi import HTTPException, status

from app.models.glosa import Glosa
from app.repositories.billing_repository import BillingRepository
from app.repositories.glosa_repository import GlosaRepository
from app.schemas.glosa import GlosaCreateRequest, GlosaReconciliationResponse, GlosaResponse, RiskLevelReconciliation
from app.schemas.pagination import PaginatedResponse

# denial_risk_engine.py só produz estes 3 níveis. "medium"/"high" contam
# como "o motor previu risco de glosa" — ver DECISÃO em
# GlosaReconciliationResponse.
_PREDICTED_RISK_LEVELS = ("medium", "high")


class GlosaService:
    def __init__(self, glosa_repo: GlosaRepository, billing_repo: BillingRepository):
        self.glosa_repo = glosa_repo
        self.billing_repo = billing_repo

    async def create_glosa(self, tenant_id: str, data: GlosaCreateRequest) -> GlosaResponse:
        billing = await self.billing_repo.get_by_id(data.billing_id)
        if billing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Faturamento não encontrado neste tenant.")

        glosa = Glosa(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            billing_id=data.billing_id,
            codigo_motivo=data.codigo_motivo,
            descricao_motivo=data.descricao_motivo,
            valor_glosado=round(data.valor_glosado, 2),
            data_recebimento=data.data_recebimento or datetime.now(timezone.utc),
        )
        saved = await self.glosa_repo.add(glosa)
        return GlosaResponse.model_validate(saved)

    async def list_glosas_paginated(self, *, limit: int, offset: int) -> PaginatedResponse[GlosaResponse]:
        items, total = await self.glosa_repo.list_paginated(limit=limit, offset=offset)
        return PaginatedResponse(items=[GlosaResponse.model_validate(i) for i in items], total=total, limit=limit, offset=offset)

    async def get_glosa(self, glosa_id: uuid.UUID) -> GlosaResponse:
        glosa = await self.glosa_repo.get_by_id(glosa_id)
        if glosa is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Glosa não encontrada neste tenant.")
        return GlosaResponse.model_validate(glosa)

    async def get_reconciliation(self, date_from: date, date_to: date) -> GlosaReconciliationResponse:
        rows = await self.glosa_repo.reconciliation_by_risk_level(date_from, date_to)
        by_level = {level: (billing_count, glosado_count, valor_total) for level, billing_count, glosado_count, valor_total in rows}

        predicted_billing_count = sum(by_level.get(level, (0, 0, 0.0))[0] for level in _PREDICTED_RISK_LEVELS)
        true_positive = sum(by_level.get(level, (0, 0, 0.0))[1] for level in _PREDICTED_RISK_LEVELS)
        false_positive = predicted_billing_count - true_positive
        valor_glosado_previsto = sum(by_level.get(level, (0, 0, 0.0))[2] for level in _PREDICTED_RISK_LEVELS)

        low_billing_count, low_glosado_count, low_valor_total = by_level.get("low", (0, 0, 0.0))
        false_negative = low_glosado_count
        true_negative = low_billing_count - low_glosado_count

        precision_pct = round(true_positive / (true_positive + false_positive) * 100, 2) if (true_positive + false_positive) > 0 else None
        recall_pct = round(true_positive / (true_positive + false_negative) * 100, 2) if (true_positive + false_negative) > 0 else None

        return GlosaReconciliationResponse(
            period_start=date_from,
            period_end=date_to,
            by_risk_level=[
                RiskLevelReconciliation(level=level, billing_count=bc, glosado_count=gc, valor_glosado_total=vt)
                for level, (bc, gc, vt) in by_level.items()
            ],
            true_positive_count=true_positive,
            false_positive_count=false_positive,
            false_negative_count=false_negative,
            true_negative_count=true_negative,
            precision_pct=precision_pct,
            recall_pct=recall_pct,
            valor_glosado_previsto=valor_glosado_previsto,
            valor_glosado_nao_previsto=low_valor_total,
        )
