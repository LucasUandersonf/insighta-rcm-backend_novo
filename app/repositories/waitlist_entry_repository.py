import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.models.professional import Professional
from app.models.waitlist_entry import WaitlistEntry


class WaitlistEntryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    _JOINED_COLUMNS = (
        WaitlistEntry.id,
        WaitlistEntry.patient_id,
        Patient.full_name.label("patient_full_name"),
        WaitlistEntry.professional_id,
        Professional.full_name.label("professional_full_name"),
        WaitlistEntry.procedure_code,
        WaitlistEntry.preferred_time_window,
        WaitlistEntry.notes,
        WaitlistEntry.status,
        WaitlistEntry.resolved_appointment_id,
        WaitlistEntry.created_at,
        WaitlistEntry.resolved_at,
    )

    def _joined_stmt(self):
        return (
            select(*self._JOINED_COLUMNS)
            .select_from(WaitlistEntry)
            .join(Patient, Patient.id == WaitlistEntry.patient_id)
            .outerjoin(Professional, Professional.id == WaitlistEntry.professional_id)
        )

    async def get_by_id(self, waitlist_entry_id: uuid.UUID) -> WaitlistEntry | None:
        stmt = select(WaitlistEntry).where(WaitlistEntry.id == waitlist_entry_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_joined_by_id(self, waitlist_entry_id: uuid.UUID) -> dict | None:
        stmt = self._joined_stmt().where(WaitlistEntry.id == waitlist_entry_id)
        result = await self.session.execute(stmt)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def list_joined(self, *, status: str | None, limit: int, offset: int) -> tuple[list[dict], int]:
        """Fila de espera pronta pra tela: nome do paciente/profissional já
        resolvidos (evita N+1 no frontend, mesmo raciocínio de
        AppointmentListItem). Mais antigo primeiro — a régua natural de
        "quem está esperando há mais tempo" numa fila."""
        base_stmt = self._joined_stmt()
        if status is not None:
            base_stmt = base_stmt.where(WaitlistEntry.status == status)

        items_stmt = base_stmt.order_by(WaitlistEntry.created_at.asc()).limit(limit).offset(offset)
        items = [dict(row) for row in (await self.session.execute(items_stmt)).mappings().all()]

        count_stmt = select(func.count()).select_from(WaitlistEntry)
        if status is not None:
            count_stmt = count_stmt.where(WaitlistEntry.status == status)
        total = (await self.session.execute(count_stmt)).scalar_one()

        return items, total

    async def add(self, entry: WaitlistEntry) -> WaitlistEntry:
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def mark_resolved(self, entry: WaitlistEntry, *, appointment_id: uuid.UUID, resolved_at: datetime) -> WaitlistEntry:
        entry.status = "agendado"
        entry.resolved_appointment_id = appointment_id
        entry.resolved_at = resolved_at
        await self.session.flush()
        return entry

    async def mark_cancelled(self, entry: WaitlistEntry, *, resolved_at: datetime) -> WaitlistEntry:
        entry.status = "cancelado"
        entry.resolved_at = resolved_at
        await self.session.flush()
        return entry
