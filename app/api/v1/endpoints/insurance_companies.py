"""
app/api/v1/endpoints/insurance_companies.py

CRUD mínimo (create + list) para alimentar o seletor "Convênio -> Plano"
da tela de Contratos (ver briefing da Inteligência de Contratos). Mesmo
RBAC de contracts.py — dado financeiro/estrutural, não é de 'atendimento'.
"""
import uuid

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.insurance_company_repository import InsuranceCompanyRepository
from app.repositories.insurance_plan_repository import InsurancePlanRepository
from app.schemas.insurance_company import (
    InsuranceCompanyCreateRequest,
    InsuranceCompanyResponse,
    InsuranceCompanyUpdateRequest,
)
from app.schemas.insurance_plan import InsurancePlanCreateRequest, InsurancePlanResponse
from app.services.insurance_company_service import InsuranceCompanyService
from app.services.insurance_plan_service import InsurancePlanService

router = APIRouter(prefix="/insurance-companies", tags=["insurance-companies"])

_CAN_WRITE = ("financeiro", "admin", "owner")
_CAN_READ = ("financeiro", "admin", "owner", "auditor", "atendimento")


@router.post("", response_model=InsuranceCompanyResponse, status_code=201)
async def create_insurance_company(
    payload: InsuranceCompanyCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> InsuranceCompanyResponse:
    service = InsuranceCompanyService(InsuranceCompanyRepository(db))
    return await service.create_company(current_user.tenant_id, payload)


@router.get("", response_model=list[InsuranceCompanyResponse])
async def list_insurance_companies(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
) -> list[InsuranceCompanyResponse]:
    service = InsuranceCompanyService(InsuranceCompanyRepository(db))
    return await service.list_companies()


@router.patch("/{company_id}", response_model=InsuranceCompanyResponse)
async def update_insurance_company(
    company_id: uuid.UUID,
    payload: InsuranceCompanyUpdateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> InsuranceCompanyResponse:
    """Hoje só serve para corrigir `default_appeal_deadline_days` depois
    de conferir o prazo real no contrato com a operadora (ver DECISÃO em
    app/sql/008_denial_appeals.sql)."""
    service = InsuranceCompanyService(InsuranceCompanyRepository(db))
    return await service.update_company(company_id, payload)


@router.post("/plans", response_model=InsurancePlanResponse, status_code=201)
async def create_insurance_plan(
    payload: InsurancePlanCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> InsurancePlanResponse:
    service = InsurancePlanService(InsurancePlanRepository(db), InsuranceCompanyRepository(db))
    return await service.create_plan(current_user.tenant_id, payload)


@router.get("/plans", response_model=list[InsurancePlanResponse])
async def list_insurance_plans(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
) -> list[InsurancePlanResponse]:
    service = InsurancePlanService(InsurancePlanRepository(db), InsuranceCompanyRepository(db))
    return await service.list_plans()
