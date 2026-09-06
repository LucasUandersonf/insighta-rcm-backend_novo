import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.core.security import generate_temporary_password, hash_password, verify_password
from app.models.user import User
from app.repositories.audit_log_repository import AuditLogRepository
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

    DECISÃO — por que TODA mutação aqui vira audit_log
    -------------------------------------------------------------------
    Criar usuário, mudar papel de acesso, ativar/desativar e resetar
    senha de outra pessoa são exatamente o tipo de ação que uma auditoria
    de conformidade (LGPD/HealthTech) precisa provar "quem fez, quando" —
    é literalmente controle de QUEM PODE ACESSAR o quê. Nenhum desses
    eventos carrega senha (nem hash) no `diff`, só o metadado da mudança
    (papel/status), pelo mesmo motivo de nunca gravar dado sensível
    duplicado no audit log (ver AuditLogRepository.record).
    """

    def __init__(self, repo: UserRepository, audit_repo: AuditLogRepository):
        self.repo = repo
        self.audit_repo = audit_repo

    async def list_users(self) -> list[UserResponse]:
        users = await self.repo.list_all()
        return [UserResponse.model_validate(u) for u in users]

    async def create_user(self, tenant_id: str, actor_user_id: uuid.UUID | None, data: UserCreateRequest) -> tuple[UserResponse, str]:
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
        await self.audit_repo.record(
            tenant_id=uuid.UUID(tenant_id),
            actor_user_id=actor_user_id,
            action="created",
            entity_type="user",
            entity_id=user.id,
            diff={"role": user.role},
        )
        return UserResponse.model_validate(user), temp_password

    async def update_user(
        self, tenant_id: str, current_user_id: str, user_id: uuid.UUID, data: UserUpdateRequest
    ) -> UserResponse:
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

        previous_role, previous_active = user.role, user.is_active
        if data.full_name is not None:
            user.full_name = data.full_name
        if data.role is not None:
            user.role = data.role
        if data.is_active is not None:
            user.is_active = data.is_active
        await self.repo.save(user)

        # Só grava se algo de fato ACESSO-RELEVANTE mudou (papel ou
        # status ativo) — uma edição que só troca full_name não é um
        # evento de controle de acesso, não precisa de trilha aqui.
        changed: dict = {}
        if user.role != previous_role:
            changed["role"] = {"before": previous_role, "after": user.role}
        if user.is_active != previous_active:
            changed["is_active"] = {"before": previous_active, "after": user.is_active}
        if changed:
            await self.audit_repo.record(
                tenant_id=uuid.UUID(tenant_id),
                actor_user_id=uuid.UUID(current_user_id),
                action="updated",
                entity_type="user",
                entity_id=user.id,
                diff=changed,
            )
        return UserResponse.model_validate(user)

    async def admin_reset_password(self, tenant_id: str, actor_user_id: uuid.UUID, user_id: uuid.UUID) -> PasswordResetResponse:
        user = await self.repo.get_by_id(user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")
        temp_password = generate_temporary_password()
        user.hashed_password = hash_password(temp_password)
        user.must_change_password = True
        await self.repo.save(user)
        # Sem diff — a senha (nem hash) nunca entra no audit log; o fato
        # de QUEM resetou a senha de QUEM já é o dado de conformidade
        # relevante aqui.
        await self.audit_repo.record(
            tenant_id=uuid.UUID(tenant_id),
            actor_user_id=actor_user_id,
            action="password_reset",
            entity_type="user",
            entity_id=user.id,
        )
        return PasswordResetResponse(temporary_password=temp_password)

    async def get_own_profile(self, user_id: uuid.UUID) -> UserResponse:
        """Perfil do próprio usuário autenticado — alimenta a identificação
        de usuário (avatar/nome) na barra superior, para QUALQUER papel
        (mesmo RBAC de change_own_password: cada um só lê a si mesmo aqui;
        gestão de outros usuários continua restrita a owner/admin em
        list_users/update_user)."""
        user = await self.repo.get_by_id(user_id)
        if user is None:
            # Não deveria acontecer (usuário autenticado por um JWT válido) —
            # mesmo raciocínio de change_own_password.
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessão inválida.")
        return UserResponse.model_validate(user)

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
