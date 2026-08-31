"""
app/repositories/reporting_repository.py

Agregações do relatório semanal (Etapa 4). Mesma filosofia de
capacity_repository.py: somas/contagens acontecem no Postgres via
func.sum/func.count, não carregando linha por linha para a aplicação.

DECISÃO — high_risk_pending_count NÃO é escopado pela semana
-------------------------------------------------------------------------
total_billed e total_value_saved medem o que ACONTECEU na semana (faturado
criado no período). Já a contagem de faturamentos held_for_review é o
BACKLOG ATUAL aguardando revisão humana, independente de quando foi
criado — é um item acionável ("isso precisa da sua atenção agora"), e
escopar por semana esconderia um item de risco alto criado há duas
semanas que ainda não foi revisado. São duas perguntas diferentes:
"o que aconteceu esta semana" vs. "o que ainda precisa de atenção".

DECISÃO — atribuição de ROI é uma simplificação deliberada
-------------------------------------------------------------------------
`revenue_from_campaign_patients` soma o faturamento da semana de
QUALQUER paciente com acquisition_campaign_id preenchido — não faz um
rastreio de coorte rigoroso (gasto de campanha X só conta receita dos
pacientes que converteram DEPOIS daquele gasto específico, dentro de uma
janela de atribuição). Para o MVP, "gasto da semana vs. receita da
semana de pacientes vindos de campanha" já dá um sinal de ROI direcional
útil; atribuição por coorte é trabalho de analytics mais maduro, fica
para quando houver validação de que o número simplificado não é
suficiente.
"""
from datetime import date, datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.billing import Billing
from app.models.marketing_spend import MarketingSpend
from app.models.patient import Patient


def _bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(date_from, time.min, tzinfo=timezone.utc),
        datetime.combine(date_to, time.max, tzinfo=timezone.utc),
    )


class ReportingRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def billing_summary(self, date_from: date, date_to: date) -> dict:
        start, end = _bounds(date_from, date_to)
        stmt = select(
            func.coalesce(func.sum(Billing.charged_value), 0),
            func.coalesce(func.sum(Billing.value_saved_by_correction), 0),
        ).where(Billing.created_at >= start, Billing.created_at <= end)
        total_billed, total_saved = (await self.session.execute(stmt)).one()

        pending_stmt = select(func.count()).where(Billing.status == "held_for_review")
        pending_count = (await self.session.execute(pending_stmt)).scalar_one()

        return {
            "total_billed": float(total_billed),
            "total_value_saved": float(total_saved),
            "high_risk_pending_count": int(pending_count),
        }

    async def marketing_spend_total(self, date_from: date, date_to: date) -> float:
        stmt = select(func.coalesce(func.sum(MarketingSpend.amount_spent), 0)).where(
            MarketingSpend.spend_date >= date_from, MarketingSpend.spend_date <= date_to
        )
        return float((await self.session.execute(stmt)).scalar_one())

    async def revenue_from_campaign_patients(self, date_from: date, date_to: date) -> float:
        start, end = _bounds(date_from, date_to)
        stmt = (
            select(func.coalesce(func.sum(Billing.charged_value), 0))
            .select_from(Billing)
            .join(Appointment, Appointment.id == Billing.appointment_id)
            .join(Patient, Patient.id == Appointment.patient_id)
            .where(
                Patient.acquisition_campaign_id.is_not(None),
                Billing.created_at >= start,
                Billing.created_at <= end,
            )
        )
        return float((await self.session.execute(stmt)).scalar_one())

    async def no_show_count(self, date_from: date, date_to: date) -> int:
        start, end = _bounds(date_from, date_to)
        stmt = select(func.count()).where(
            Appointment.status == "no_show", Appointment.scheduled_at >= start, Appointment.scheduled_at <= end
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def upcoming_high_risk_appointments_count(self, date_from: date, date_to: date) -> int:
        start, end = _bounds(date_from, date_to)
        stmt = select(func.count()).where(
            Appointment.status == "scheduled",
            Appointment.no_show_risk_level == "alto",
            Appointment.scheduled_at >= start,
            Appointment.scheduled_at <= end,
        )
        return int((await self.session.execute(stmt)).scalar_one())
