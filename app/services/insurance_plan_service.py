"""
app/services/insurance_plan_service.py

Cadastro de plano PELA TELA — diferente de
InsurancePlanRepository.resolve()/record_alias_if_new (usados
internamente pelo pipeline de ingestão para resolver texto cru do
arquivo importado). Aqui é um humano escolhendo explicitamente "Convênio
X, Plano Y" na tela de Contratos, então normalized_key é derivado do
display_name com o mesmo slugify usado na ingestão — garante que um
plano cadastrado manualmente hoje já "bata" por normalized_key se o
mesmo nome aparecer num arquivo importado amanhã.
"""
import uuid

from fastapi import HTTPException, status

from app.core.text_utils import slugify
from app.models.insurance_plan import InsurancePlan
from app.repositories.insurance_company_repository import InsuranceCompanyRepository
from app.repositories.insurance_plan_repository import InsurancePlanRepository
from app.schemas.insurance_plan import InsurancePlanCreateRequest, InsurancePlanResponse, InsurancePlanUpdateRequest


class InsurancePlanService:
    def __init__(self, repo: InsurancePlanRepository, company_repo: InsuranceCompanyRepository):
        self.repo = repo
        self.company_repo = company_repo

    async def create_plan(self, tenant_id: str, data: InsurancePlanCreateRequest) -> InsurancePlanResponse:
        company = await self.company_repo.get_by_id(data.insurance_company_id)
        if company is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operadora não encontrada neste tenant.")

        plan = InsurancePlan(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            insurance_company_id=data.insurance_company_id,
            display_name=data.display_name,
            normalized_key=slugify(data.display_name),
            ans_registry=data.ans_registry,
        )
        saved = await self.repo.add(plan)
        return InsurancePlanResponse.model_validate(saved)

    async def list_plans(self, *, include_inactive: bool = False) -> list[InsurancePlanResponse]:
        plans = await (self.repo.list_all() if include_inactive else self.repo.list_active())
        return [InsurancePlanResponse.model_validate(p) for p in plans]

    async def update_plan(self, plan_id: uuid.UUID, data: InsurancePlanUpdateRequest) -> InsurancePlanResponse:
        """Hoje só existe para desativar/reativar — mesmo padrão de
        ProfessionalService.update_professional (só aplica campos
        explicitamente enviados)."""
        plan = await self.repo.get_by_id(plan_id)
        if plan is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plano não encontrado neste tenant.")
        if data.is_active is not None:
            plan.is_active = data.is_active
        await self.repo.save(plan)
        return InsurancePlanResponse.model_validate(plan)
