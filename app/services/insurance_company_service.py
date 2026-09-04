import uuid

from fastapi import HTTPException, status

from app.models.insurance_company import InsuranceCompany
from app.repositories.insurance_company_repository import InsuranceCompanyRepository
from app.schemas.insurance_company import (
    InsuranceCompanyCreateRequest,
    InsuranceCompanyResponse,
    InsuranceCompanyUpdateRequest,
)


class InsuranceCompanyService:
    def __init__(self, repo: InsuranceCompanyRepository):
        self.repo = repo

    async def create_company(self, tenant_id: str, data: InsuranceCompanyCreateRequest) -> InsuranceCompanyResponse:
        company = InsuranceCompany(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            name=data.name,
            ans_registry=data.ans_registry,
            default_appeal_deadline_days=data.default_appeal_deadline_days,
        )
        saved = await self.repo.add(company)
        return InsuranceCompanyResponse.model_validate(saved)

    async def list_companies(self, *, include_inactive: bool = False) -> list[InsuranceCompanyResponse]:
        companies = await (self.repo.list_all() if include_inactive else self.repo.list_active())
        return [InsuranceCompanyResponse.model_validate(c) for c in companies]

    async def update_company(self, company_id: uuid.UUID, data: InsuranceCompanyUpdateRequest) -> InsuranceCompanyResponse:
        """
        BUG CORRIGIDO de passagem — antes de `is_active` existir, esta
        função já tinha o mesmo defeito latente que motivou o padrão
        "só aplica o que foi enviado" em ProfessionalService/UserService:
        `default_appeal_deadline_days` era reatribuído incondicionalmente
        com `data.default_appeal_deadline_days`, que é `None` sempre que o
        campo não é enviado no PATCH — um PATCH só de `is_active` (o caso
        novo desta mudança) apagaria silenciosamente o prazo já
        configurado. Corrigido para só aplicar campos explicitamente
        enviados, como as demais entidades já fazem.
        """
        company = await self.repo.get_by_id(company_id)
        if company is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operadora não encontrada neste tenant.")
        if data.default_appeal_deadline_days is not None:
            company.default_appeal_deadline_days = data.default_appeal_deadline_days
        if data.is_active is not None:
            company.is_active = data.is_active
        await self.repo.save(company)
        return InsuranceCompanyResponse.model_validate(company)
