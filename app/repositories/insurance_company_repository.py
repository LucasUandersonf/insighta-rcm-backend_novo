import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.insurance_company import InsuranceCompany


class InsuranceCompanyRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self) -> list[InsuranceCompany]:
        stmt = select(InsuranceCompany).order_by(InsuranceCompany.name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, company_id: uuid.UUID) -> InsuranceCompany | None:
        stmt = select(InsuranceCompany).where(InsuranceCompany.id == company_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def add(self, company: InsuranceCompany) -> InsuranceCompany:
        self.session.add(company)
        await self.session.flush()
        return company

    async def save(self, company: InsuranceCompany) -> InsuranceCompany:
        await self.session.flush()
        return company
