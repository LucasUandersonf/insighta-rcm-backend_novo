"""
app/repositories/insurance_plan_repository.py

Resolve o texto cru de convênio vindo do arquivo importado ("UNIMED
NAC.") para o insurance_plan_id correto, e mantém o histórico de
variações em insurance_plan_aliases — exatamente o propósito documentado
para essa tabela em 001_init_schema.sql.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.insurance_plan import InsurancePlan
from app.models.insurance_plan_alias import InsurancePlanAlias


class InsurancePlanRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, plan_id: uuid.UUID) -> InsurancePlan | None:
        result = await self.session.execute(select(InsurancePlan).where(InsurancePlan.id == plan_id))
        return result.scalar_one_or_none()

    async def list_active(self) -> list[InsurancePlan]:
        """Alimenta os seletores de cadastro NOVO (Contratos, Central de
        Upload) — mesmo critério de ProfessionalRepository.list_active."""
        result = await self.session.execute(
            select(InsurancePlan).where(InsurancePlan.is_active.is_(True)).order_by(InsurancePlan.display_name)
        )
        return list(result.scalars().all())

    async def list_all(self) -> list[InsurancePlan]:
        """Ativos e inativos — usado pela tela de gestão (precisa ver e
        poder reativar quem foi desativado; `list_active` continua sendo
        o que alimenta seletores operacionais)."""
        result = await self.session.execute(select(InsurancePlan).order_by(InsurancePlan.display_name))
        return list(result.scalars().all())

    async def add(self, plan: InsurancePlan) -> InsurancePlan:
        self.session.add(plan)
        await self.session.flush()
        return plan

    async def save(self, plan: InsurancePlan) -> InsurancePlan:
        await self.session.flush()
        return plan

    async def resolve(self, raw_name: str, normalized_key: str) -> InsurancePlan | None:
        """
        Duas tentativas, nesta ordem:
          1) Match direto por normalized_key (o caso comum: o texto do
             arquivo já bate com a forma canônica cadastrada).
          2) Match por uma variação já vista antes em
             insurance_plan_aliases.raw_value (ex: "UNIMED NAC." já foi
             mapeado manualmente para o convênio certo em uma importação
             anterior, mesmo que o slug automático não bata).
        Se nenhuma das duas encontrar, retorna None — a linha fica
        pendente de revisão humana, nunca inventamos um convênio novo
        sozinhos (ver DECISÃO em normalization_service.py).
        """
        by_key = await self.session.execute(
            select(InsurancePlan).where(InsurancePlan.normalized_key == normalized_key)
        )
        plan = by_key.scalar_one_or_none()
        if plan is not None:
            return plan

        by_alias = await self.session.execute(
            select(InsurancePlan)
            .join(InsurancePlanAlias, InsurancePlanAlias.insurance_plan_id == InsurancePlan.id)
            .where(InsurancePlanAlias.raw_value == raw_name)
        )
        return by_alias.scalar_one_or_none()

    async def record_alias_if_new(self, *, tenant_id: uuid.UUID, plan_id: uuid.UUID, raw_name: str, source_file: str | None) -> None:
        """
        Registra esta variação de texto para acelerar o match na próxima
        importação, mas só se ainda não existir — evita entupir a tabela
        de aliases com a mesma string repetida a cada arquivo diário.
        """
        existing = await self.session.execute(
            select(InsurancePlanAlias).where(
                InsurancePlanAlias.insurance_plan_id == plan_id,
                InsurancePlanAlias.raw_value == raw_name,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return
        self.session.add(
            InsurancePlanAlias(
                tenant_id=tenant_id,
                insurance_plan_id=plan_id,
                raw_value=raw_name,
                source_file=source_file,
            )
        )
        await self.session.flush()
