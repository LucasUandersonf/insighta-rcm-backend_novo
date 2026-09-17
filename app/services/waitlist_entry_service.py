"""
app/services/waitlist_entry_service.py

Onda 5 do Plano de Ação, item 16 — ver DECISÃO completa em
app/sql/057_waitlist_entries.sql.
"""
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.models.waitlist_entry import WaitlistEntry
from app.repositories.patient_repository import PatientRepository
from app.repositories.professional_repository import ProfessionalRepository
from app.repositories.waitlist_entry_repository import WaitlistEntryRepository
from app.schemas.pagination import PaginatedResponse
from app.schemas.waitlist_entry import WaitlistEntryCreateRequest, WaitlistEntryResponse


class WaitlistEntryService:
    def __init__(
        self,
        repo: WaitlistEntryRepository,
        patient_repo: PatientRepository,
        professional_repo: ProfessionalRepository,
    ):
        self.repo = repo
        self.patient_repo = patient_repo
        self.professional_repo = professional_repo

    async def create_waitlist_entry(
        self, tenant_id: str, created_by: uuid.UUID, data: WaitlistEntryCreateRequest
    ) -> WaitlistEntryResponse:
        patient = await self.patient_repo.get_by_id(data.patient_id)
        if patient is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paciente não encontrado neste tenant.")
        if data.professional_id is not None:
            professional = await self.professional_repo.get_by_id(data.professional_id)
            if professional is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profissional não encontrado neste tenant.")

        entry = WaitlistEntry(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            patient_id=data.patient_id,
            professional_id=data.professional_id,
            procedure_code=data.procedure_code,
            preferred_time_window=data.preferred_time_window,
            notes=data.notes,
            status="aguardando",
            created_by=created_by,
        )
        saved = await self.repo.add(entry)
        joined = await self.repo.get_joined_by_id(saved.id)
        return WaitlistEntryResponse(**joined)

    async def list_waitlist_entries(self, *, status_filter: str | None, limit: int, offset: int) -> PaginatedResponse[WaitlistEntryResponse]:
        items, total = await self.repo.list_joined(status=status_filter, limit=limit, offset=offset)
        return PaginatedResponse(
            items=[WaitlistEntryResponse(**item) for item in items], total=total, limit=limit, offset=offset
        )

    async def _get_active_entry_or_404(self, waitlist_entry_id: uuid.UUID) -> WaitlistEntry:
        entry = await self.repo.get_by_id(waitlist_entry_id)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entrada de lista de espera não encontrada neste tenant.")
        if entry.status != "aguardando":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Esta entrada já foi resolvida (status atual: {entry.status}).",
            )
        return entry

    async def resolve_waitlist_entry(self, waitlist_entry_id: uuid.UUID, appointment_id: uuid.UUID) -> WaitlistEntryResponse:
        entry = await self._get_active_entry_or_404(waitlist_entry_id)
        await self.repo.mark_resolved(entry, appointment_id=appointment_id, resolved_at=datetime.now(timezone.utc))
        joined = await self.repo.get_joined_by_id(entry.id)
        return WaitlistEntryResponse(**joined)

    async def cancel_waitlist_entry(self, waitlist_entry_id: uuid.UUID) -> WaitlistEntryResponse:
        entry = await self._get_active_entry_or_404(waitlist_entry_id)
        await self.repo.mark_cancelled(entry, resolved_at=datetime.now(timezone.utc))
        joined = await self.repo.get_joined_by_id(entry.id)
        return WaitlistEntryResponse(**joined)
