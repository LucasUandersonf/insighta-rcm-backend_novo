"""
app/repositories/billing_repository.py

Repositório recebe a `session` já tenant-aware (ver DbSession em deps.py)
e NUNCA precisa escrever `.where(Billing.tenant_id == ...)` manualmente —
essa é justamente a folga que o RLS nos dá: o repositório fica mais
simples e, ao mesmo tempo, mais seguro, porque não depende de um
desenvolvedor lembrar de filtrar por tenant em toda query nova.
"""
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.billing import Billing
from app.models.insurance_plan import InsurancePlan
from app.models.patient import Patient


class BillingRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_high_risk(self) -> list[Billing]:
        # Sem WHERE tenant_id: o RLS já garante que só vêm linhas do
        # tenant certo. Isso alimenta a Tela B (Painel Anti-Glosa).
        stmt = select(Billing).where(Billing.denial_risk_level == "high")
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_high_risk_paginated(
        self, *, limit: int, offset: int, insurance_plan_id: uuid.UUID | None = None
    ) -> tuple[list[Billing], int]:
        # Mesmo padrão de paginação aplicado a contracts/denial-appeals/
        # patients (ver PaginatedResponse em app/schemas/pagination.py):
        # itens + contagem total, para a UI renderizar "Mostrando X-Y de Z".
        #
        # `insurance_plan_id` opcional (Sala de Comando 2.0, item 4 do
        # roadmap "botão de ação real"): o insight de recusa em alta
        # agora linka direto pra AQUI, já filtrado pelo convênio exato
        # que disparou o card — antes sempre caía na fila GERAL, e o
        # usuário tinha que procurar sozinho quais linhas eram daquele
        # convênio (ver DECISÃO em smart_insights_engine.py::_denial_spike_insights).
        base = select(Billing).where(Billing.denial_risk_level == "high")
        if insurance_plan_id is not None:
            base = base.where(Billing.insurance_plan_id == insurance_plan_id)
        total = (await self.session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        stmt = base.order_by(Billing.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def get_by_id(self, billing_id: uuid.UUID) -> Billing | None:
        stmt = select(Billing).where(Billing.id == billing_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_guia(self, guia_id: uuid.UUID) -> list[Billing]:
        """Usado pela normalização do Template de Integração "Glosa" (ver
        NormalizationService.normalize_glosa_row) para achar a(s) linha(s)
        de billing que uma guia agrupa — 1 quando a guia tem um único
        procedimento (caso comum), N quando é uma SADT com vários itens
        (nesse caso, quem chama precisa desambiguar por procedure_code —
        ver list_by_guia_and_procedure_code)."""
        stmt = select(Billing).where(Billing.guia_id == guia_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def search(self, query: str, *, limit: int = 20) -> list[tuple[Billing, str, str | None, str]]:
        """
        Busca por nome/CPF do paciente — a tela de Recurso de Glosa (e
        qualquer outra que hoje pede o billing_id colado como UUID cru)
        usa isso para deixar o usuário procurar "Maria Silva" em vez de
        precisar saber o UUID interno de cor. Devolve
        (Billing, nome_do_paciente, codigo_procedimento, nome_do_convenio)
        já resolvidos — um único round-trip, sem N+1 no chamador.
        """
        digits = "".join(ch for ch in query if ch.isdigit())
        stmt = (
            select(Billing, Patient.full_name, Appointment.procedure_code, InsurancePlan.display_name)
            .join(Appointment, Appointment.id == Billing.appointment_id)
            .join(Patient, Patient.id == Appointment.patient_id)
            .join(InsurancePlan, InsurancePlan.id == Billing.insurance_plan_id)
            .where(
                or_(
                    Patient.full_name.ilike(f"%{query}%"),
                    (Patient.cpf == digits) if digits else False,
                )
            )
            .order_by(Billing.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.all())

    async def list_by_guia_and_procedure_code(self, guia_id: uuid.UUID, procedure_code: str) -> list[Billing]:
        """Desambigua entre as várias linhas de billing de uma mesma guia
        pelo código de procedimento do atendimento associado — é assim
        que o demonstrativo de pagamento identifica QUAL item de uma SADT
        com múltiplos procedimentos está sendo liquidado."""
        stmt = (
            select(Billing)
            .join(Appointment, Appointment.id == Billing.appointment_id)
            .where(Billing.guia_id == guia_id, Appointment.procedure_code == procedure_code)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add(self, billing: Billing) -> Billing:
        self.session.add(billing)
        await self.session.flush()  # garante que billing.id exista antes do commit implícito
        return billing

    async def save(self, billing: Billing) -> Billing:
        await self.session.flush()
        return billing
