import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.professional import Professional


class ProfessionalRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_active(self) -> list[Professional]:
        stmt = select(Professional).where(Professional.is_active.is_(True)).order_by(Professional.full_name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_all(self) -> list[Professional]:
        """Ativos e inativos — usado pela Tela de Profissionais (admin
        precisa ver e poder reativar quem foi desativado; `list_active`
        continua sendo o que alimenta seletores operacionais, como o
        combobox de profissional em Nova Consulta)."""
        stmt = select(Professional).order_by(Professional.full_name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, professional_id: uuid.UUID) -> Professional | None:
        stmt = select(Professional).where(Professional.id == professional_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_registry(self, professional_registry: str) -> Professional | None:
        """Usado pela normalização de ingestão (achado F-02, Auditoria
        Go-Live) para casar o profissional de uma nova linha com um já
        existente pelo registro profissional (CRM/CRO/etc — mais estável
        que nome, que pode ter grafias diferentes entre arquivos)."""
        stmt = select(Professional).where(Professional.professional_registry == professional_registry)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_name(self, full_name: str) -> Professional | None:
        """Fallback quando a linha não trouxe registro profissional —
        mesma limitação que PatientRepository.get_by_cpf tem hoje: sem um
        identificador estável, duas linhas com o mesmo profissional mas
        pequenas diferenças de grafia podem gerar registros duplicados.
        Aceito por ora pelo mesmo motivo (ver normalization_service.py)."""
        stmt = select(Professional).where(Professional.full_name == full_name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def add(self, professional: Professional) -> Professional:
        self.session.add(professional)
        await self.session.flush()
        return professional

    async def save(self, professional: Professional) -> Professional:
        await self.session.flush()
        return professional
