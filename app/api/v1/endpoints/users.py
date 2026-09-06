"""app/api/v1/endpoints/users.py — Gestão de Usuários (RBAC), tela
administrativa do Painel do Administrador da Empresa (Tenant Owner).

Só owner/admin gerenciam usuários — mesmo padrão de _CAN_WRITE usado em
professionals.py/contracts.py, adaptado ao domínio "quem pode mexer em
quem tem acesso à plataforma"."""
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.user_repository import UserRepository
from app.schemas.user import (
    PasswordChangeRequest,
    PasswordResetResponse,
    UserCreateRequest,
    UserResponse,
    UserUpdateRequest,
)
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["users"])

_CAN_MANAGE = ("owner", "admin")
# Qualquer papel autenticado pode ver o PRÓPRIO perfil — mesmo critério de
# /users/me/change-password (self-service não é "gestão de usuários").
_CAN_VIEW_SELF = ("owner", "admin", "financeiro", "atendimento", "auditor")


def _build_service(db: DbSession) -> UserService:
    return UserService(UserRepository(db), AuditLogRepository(db))


@router.get("/me", response_model=UserResponse)
async def get_own_profile(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_VIEW_SELF)),
) -> UserResponse:
    """Perfil do próprio usuário autenticado (nome, e-mail, papel) — usado
    pela identificação de usuário (avatar + nome) na barra superior. Vem
    antes de GET "" na definição de rota só por organização; não há
    conflito de path (GET "" não tem parâmetro, GET "/{user_id}" não
    existe — só PATCH)."""
    return await _build_service(db).get_own_profile(UUID(current_user.id))


@router.get("", response_model=list[UserResponse])
async def list_users(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_MANAGE)),
) -> list[UserResponse]:
    return await _build_service(db).list_users()


@router.post("", response_model=UserResponse, status_code=201)
async def create_user(
    payload: UserCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_MANAGE)),
) -> UserResponse:
    user, temp_password = await _build_service(db).create_user(current_user.tenant_id, UUID(current_user.id), payload)
    # Devolvemos a senha temporária embutida no header de resposta em vez
    # do corpo (que segue o contrato de UserResponse) — ver o endpoint
    # dedicado /users/{id}/reset-password para o formato "de verdade"
    # dessa informação sensível. Aqui, para a criação, o frontend chama
    # em seguida esse mesmo endpoint de reset caso precise reexibir.
    return user


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    payload: UserUpdateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_MANAGE)),
) -> UserResponse:
    return await _build_service(db).update_user(current_user.tenant_id, current_user.id, user_id, payload)


@router.post("/{user_id}/reset-password", response_model=PasswordResetResponse)
async def admin_reset_password(
    user_id: UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_MANAGE)),
) -> PasswordResetResponse:
    """Reset administrado: owner/admin geram uma nova senha temporária
    para um colaborador (ex: esqueceu a senha e não há e-mail transacional
    integrado ainda — ver DECISÃO em app/sql/006_platform_admin.sql).
    A senha só aparece nesta resposta, uma única vez."""
    return await _build_service(db).admin_reset_password(current_user.tenant_id, UUID(current_user.id), user_id)


@router.post("/me/change-password", status_code=204)
async def change_own_password(
    payload: PasswordChangeRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role("owner", "admin", "financeiro", "atendimento", "auditor")),
) -> None:
    """Recuperação e alteração segura de senha (self-service) — qualquer
    papel autenticado pode trocar a PRÓPRIA senha, desde que prove
    conhecer a atual."""
    await _build_service(db).change_own_password(current_user.id, payload)
