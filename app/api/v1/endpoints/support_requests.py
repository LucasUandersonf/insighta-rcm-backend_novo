"""
app/api/v1/endpoints/support_requests.py — Central de Ajuda, "tirar
dúvida sem sair do sistema". Ver DECISÃO completa em
app/sql/023_announcements_and_support.sql e support_request_service.py.

Mesmo critério de RBAC de announcements.py: qualquer papel autenticado
pode mandar uma pergunta e ver o histórico do PRÓPRIO tenant — não é
dado financeiro/clínico sensível.
"""
import uuid

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.support_request_repository import SupportRequestRepository
from app.repositories.tenant_repository import TenantRepository
from app.repositories.user_repository import UserRepository
from app.schemas.support_request import SupportRequestCreateRequest, SupportRequestResponse
from app.services.support_request_service import SupportRequestService

router = APIRouter(prefix="/support-requests", tags=["support-requests"])

_ANY_ROLE = ("owner", "admin", "financeiro", "atendimento", "auditor")


def _build_service(db: DbSession) -> SupportRequestService:
    return SupportRequestService(SupportRequestRepository(db), UserRepository(db), TenantRepository(db))


@router.post("", response_model=SupportRequestResponse, status_code=201)
async def create_support_request(
    payload: SupportRequestCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_ANY_ROLE)),
) -> SupportRequestResponse:
    return await _build_service(db).create_request(current_user.tenant_id, uuid.UUID(current_user.id), payload)


@router.get("", response_model=list[SupportRequestResponse])
async def list_support_requests(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_ANY_ROLE)),
) -> list[SupportRequestResponse]:
    """Histórico do TENANT inteiro (não só do usuário que perguntou) —
    útil para o admin/owner ver o que a equipe já perguntou, mesmo
    critério de "caixa compartilhada" de core.report_recipients."""
    return await _build_service(db).list_own_tenant()
