"""
app/services/insight_outcome_service.py

Plano Diretor Insighta, épicos F1.2 (ciclo fechado de insight) + F1.3
(atribuição/workflow) — ver DECISÃO completa em
app/sql/038_insight_outcomes.sql.
"""
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.core.text_utils import slugify
from app.models.insight_outcome import InsightOutcome
from app.repositories.insight_outcome_repository import InsightOutcomeRepository
from app.schemas.insight_outcome import (
    InsightOutcomeCreateRequest,
    InsightOutcomeResponse,
    InsightOutcomesRealizedSummary,
    InsightOutcomeUpdateRequest,
)
from app.schemas.pagination import PaginatedResponse

# Papéis que podem criar/atribuir/reconfigurar qualquer outcome — mesmo
# critério de _CAN_WRITE em lotes.py/denial_appeals.py (dado financeiro/
# estratégico, decisão de gestor). Um usuário FORA desse conjunto só
# pode mexer num outcome que já está atribuído A ELE, e só o campo
# `status`/`resolution_note` (ver update_outcome) — é o "quem executa
# não é quem decide" que o épico F1.3 descreve.
_MANAGER_ROLES = ("owner", "admin", "financeiro")


class InsightOutcomeService:
    def __init__(self, repo: InsightOutcomeRepository):
        self.repo = repo

    async def create_outcome(
        self, tenant_id: str, created_by: uuid.UUID, data: InsightOutcomeCreateRequest
    ) -> InsightOutcomeResponse:
        outcome = InsightOutcome(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            insight_key=slugify(f"{data.category}:{data.title}"),
            source=data.source,
            category=data.category,
            severity=data.severity,
            title=data.title,
            message=data.message,
            financial_impact_snapshot=data.financial_impact,
            status="pendente",
            assigned_to=data.assigned_to,
            due_date=data.due_date,
            created_by=created_by,
        )
        saved = await self.repo.add(outcome)
        return InsightOutcomeResponse.model_validate(saved)

    async def list_outcomes(
        self, *, limit: int, offset: int, status_filter: str | None = None, assigned_to: uuid.UUID | None = None
    ) -> PaginatedResponse[InsightOutcomeResponse]:
        items, total = await self.repo.list_paginated(
            limit=limit, offset=offset, status_filter=status_filter, assigned_to=assigned_to
        )
        return PaginatedResponse(
            items=[InsightOutcomeResponse.model_validate(i) for i in items], total=total, limit=limit, offset=offset
        )

    async def get_outcome(self, outcome_id: uuid.UUID) -> InsightOutcomeResponse:
        outcome = await self._get_or_404(outcome_id)
        return InsightOutcomeResponse.model_validate(outcome)

    async def get_realized_summary(self) -> InsightOutcomesRealizedSummary:
        """Painel "Insights que valeram a pena" (F1.2) — mesmo espírito
        do card de valor protegido pelo motor anti-glosa, generalizado
        pra qualquer categoria que passou pelo ciclo fechado."""
        outcomes = await self.repo.list_resolved_and_reevaluated()
        total_delta = 0.0
        for o in outcomes:
            if o.financial_impact_snapshot is not None and o.resolved_metric_value is not None:
                total_delta += float(o.financial_impact_snapshot) - float(o.resolved_metric_value)
        return InsightOutcomesRealizedSummary(
            total_resolved_and_reevaluated=len(outcomes),
            total_delta_realized=round(total_delta, 2),
            items=[InsightOutcomeResponse.model_validate(o) for o in outcomes],
        )

    async def update_outcome(
        self, *, actor_user_id: uuid.UUID, actor_role: str, outcome_id: uuid.UUID, data: InsightOutcomeUpdateRequest
    ) -> InsightOutcomeResponse:
        outcome = await self._get_or_404(outcome_id)
        is_manager = actor_role in _MANAGER_ROLES
        is_assignee = outcome.assigned_to is not None and outcome.assigned_to == actor_user_id

        if not is_manager and not is_assignee:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Só quem gerencia insights ou a pessoa atribuída pode alterar este item.",
            )
        if not is_manager:
            # Quem executa (não gerencia) só mexe no próprio progresso —
            # nunca reatribui pra outra pessoa nem muda o prazo que o
            # gestor definiu.
            if data.assigned_to is not None or data.due_date is not None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Só quem gerencia insights pode reatribuir ou mudar o prazo.",
                )

        previous_status = outcome.status
        if data.status is not None:
            outcome.status = data.status
            if data.status == "resolvido" and previous_status != "resolvido":
                outcome.resolved_at = datetime.now(timezone.utc)
            elif data.status != "resolvido" and previous_status == "resolvido":
                # Reabriu um item que já tinha sido marcado resolvido —
                # reseta o ciclo de reavaliação (F1.2), nunca deixa um
                # resolved_metric_value órfão de um resolved_at que não
                # existe mais.
                outcome.resolved_at = None
                outcome.resolved_metric_value = None
                outcome.reevaluated_at = None
        if is_manager and data.assigned_to is not None:
            outcome.assigned_to = data.assigned_to
        if is_manager and data.due_date is not None:
            outcome.due_date = data.due_date
        if data.resolution_note is not None:
            outcome.resolution_note = data.resolution_note

        saved = await self.repo.save(outcome)
        return InsightOutcomeResponse.model_validate(saved)

    async def _get_or_404(self, outcome_id: uuid.UUID) -> InsightOutcome:
        outcome = await self.repo.get_by_id(outcome_id)
        if outcome is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item não encontrado neste tenant.")
        return outcome
