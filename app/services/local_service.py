"""
app/services/local_service.py

Local de Atendimento (Unidade/Setor) — ver DECISÃO completa em
app/models/local.py e app/sql/018_locais_tipo_paciente.sql.
"""
import uuid

from fastapi import HTTPException, status

from app.models.local import Local
from app.repositories.local_repository import LocalRepository
from app.schemas.local import LocalCreateRequest, LocalResponse, LocalUpdateRequest


class LocalService:
    def __init__(self, repo: LocalRepository):
        self.repo = repo

    async def create_local(self, tenant_id: str, data: LocalCreateRequest) -> LocalResponse:
        local = Local(id=uuid.uuid4(), tenant_id=uuid.UUID(tenant_id), nome=data.nome)
        saved = await self.repo.add(local)
        return LocalResponse.model_validate(saved)

    async def list_locais(self, *, include_inactive: bool = False) -> list[LocalResponse]:
        items = await (self.repo.list_all() if include_inactive else self.repo.list_active())
        return [LocalResponse.model_validate(i) for i in items]

    async def update_local(self, local_id: uuid.UUID, data: LocalUpdateRequest) -> LocalResponse:
        local = await self.repo.get_by_id(local_id)
        if local is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Local não encontrado neste tenant.")
        if data.nome is not None:
            local.nome = data.nome
        if data.is_active is not None:
            local.is_active = data.is_active
        await self.repo.save(local)
        return LocalResponse.model_validate(local)
