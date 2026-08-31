import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.core.security import generate_temporary_password, hash_password, verify_password
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.user import (
    PasswordChangeRequest,
    PasswordResetResponse,
    UserCreateRequest,
    UserResponse,
    UserUpdateRequest,
)


class UserService:
    """Gestão de Usuários (RBAC) — Painel do Administrador da Empresa.

    DECISÃO — por que o service (não o endpoint) impede um admin de se
    autodesativar / autorrebaixar
    -------------------------------------------------------------------
    "Você não pode desativar a própria conta nem tirar seu próprio papel
    de owner" é regra de negócio (evita a clínica ficar sem nenhum owner
    ativo, um estado de sistema inválido), não uma checagem de
    autorização de rota — por isso vive aqui, não em require_role().
    """

    def __init__(self, repo: UserRepository):
        self.repo = repo

    async def list_users(self) -> list[UserResponse]:
        users = await self.repo.list_all()
        return [UserResponse.model_validate(u) for u in users]

    async def create_user(self, tenant_id: str, data: UserCreateRequest) -> tuple[UserResponse, str]:
        existing = await self.repo.get_by_email(data.email)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Já existe um usuário com este e-mail nesta clínica.",
            )
        temp_password = generate_temporary_password()
        user = await self.repo.add(
            User(
                id=uuid.uuid4(),
                tenant_id=uuid.UUID(tenant_id),
                email=data.email,
                full_name=data.full_name,
                role=data.role,
                hashed_password=hash_password(temp_password),
                must_change_password=True,
            )
        )
        return UserResponse.model_validate(user), temp_password

    async def update_user(self, current_user_id: str, user_id: uuid.UUID, data: UserUpdateRequest) -> UserResponse:
        user = await self.repo.get_by_id(user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")

        is_self = str(user.id) == current_user_id
        if is_self and data.is_active is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Você não pode desativar a própria conta.",
            )
        if is_self and data.role is not None and data.role != user.role:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Você não pode alterar seu próprio papel de acesso — peça a outro owner/admin.",
            )

        if data.full_name is not None:
            user.full_name = data.full_name
        if data.role is not None:
            user.role = data.role
        if data.is_active is not None:
            user.is_active = data.is_active
        await self.repo.save(user)
        return UserResponse.model_validate(user)

    async def admin_reset_password(self, user_id: uuid.UUID) -> PasswordResetResponse:
        user = await self.repo.get_by_id(user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")
        temp_password = generate_temporary_password()
        user.hashed_password = hash_password(temp_password)
        user.must_change_password = True
        await self.repo.save(user)
        return PasswordResetResponse(temporary_password=temp_password)

    async def change_own_password(self, user_id: str, data: PasswordChangeRequest) -> None:
        user = await self.repo.get_by_id(uuid.UUID(user_id))
        if user is None:
            # Não deveria acontecer (usuário autenticado por um JWT válido) —
            # mas se acontecer (conta apagada entre a emissão do token e
            # esta chamada), 401 é mais correto que 404 aqui.
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessão inválida.")
        if not verify_password(data.current_password, user.hashed_password):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Senha atual incorreta.")
        user.hashed_password = hash_password(data.new_password)
        user.must_change_password = False
        user.password_updated_at = datetime.now(timezone.utc)
        await self.repo.save(user)
