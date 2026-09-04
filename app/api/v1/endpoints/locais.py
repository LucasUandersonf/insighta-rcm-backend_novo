"""
app/api/v1/endpoints/locais.py

Local de Atendimento (Unidade/Setor) — Fase 4 do plano de adequação ao
fluxo real de mercado. Cadastro é decisão administrativa da clínica,
mesmo critério de professionals.py; leitura liberada para quem cria
agendamento (mesma role de appointments.py) e financeiro/auditor, que
usam local como filtro de relatório.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.local_repository import LocalRepository
from app.schemas.local import LocalCreateRequest, LocalResponse, LocalUpdateRequest
from app.services.local_service import LocalService

router = APIRouter(prefix="/locais", tags=["locais"])

_CAN_WRITE = ("admin", "owner")
_CAN_READ = (*_CAN_WRITE, "atendimento", "financeiro", "auditor")


def _build_service(db: DbSession) -> LocalService:
    return LocalService(LocalRepository(db))


@router.post("", response_model=LocalResponse, status_code=201)
async def create_local(
    payload: LocalCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> LocalResponse:
    return await _build_service(db).create_local(current_user.tenant_id, payload)


@router.get("", response_model=list[LocalResponse])
async def list_locais(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
    # False (padrão) alimenta seletores operacionais (campo "Local" em
    # Nova Consulta) — nunca deve oferecer um local desativado para um
    # agendamento novo. True é só para a tela de gestão.
    include_inactive: bool = Query(False),
) -> list[LocalResponse]:
    return await _build_service(db).list_locais(include_inactive=include_inactive)


@router.patch("/{local_id}", response_model=LocalResponse)
async def update_local(
    local_id: UUID,
    payload: LocalUpdateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> LocalResponse:
    return await _build_service(db).update_local(local_id, payload)
