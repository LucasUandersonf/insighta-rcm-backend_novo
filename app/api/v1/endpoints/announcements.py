"""
app/api/v1/endpoints/announcements.py — Central de Notificações (sino de
novidades/changelog). Ver DECISÃO completa em
app/sql/023_announcements_and_support.sql.

Sem RBAC restrito de propósito (qualquer papel autenticado pode ler e
marcar como lida) — é conteúdo informativo da plataforma, não dado
sensível de tenant; mesmo critério de app/api/v1/endpoints/users.py
para GET /users/me.
"""
import uuid

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.announcement_repository import AnnouncementRepository
from app.schemas.announcement import AnnouncementListResponse
from app.services.announcement_service import AnnouncementService

router = APIRouter(prefix="/announcements", tags=["announcements"])

_ANY_ROLE = ("owner", "admin", "financeiro", "atendimento", "auditor")


def _build_service(db: DbSession) -> AnnouncementService:
    return AnnouncementService(AnnouncementRepository(db))


@router.get("", response_model=AnnouncementListResponse)
async def list_announcements(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_ANY_ROLE)),
) -> AnnouncementListResponse:
    return await _build_service(db).list_for_user(uuid.UUID(current_user.id))


@router.post("/{announcement_id}/read", status_code=204)
async def mark_announcement_read(
    announcement_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_ANY_ROLE)),
) -> None:
    await _build_service(db).mark_read(current_user.tenant_id, uuid.UUID(current_user.id), announcement_id)
