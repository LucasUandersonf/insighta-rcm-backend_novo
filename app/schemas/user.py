from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, field_validator

from app.core.security import validate_password_strength

# Mesmo enum de app/models/user.py — repetido aqui (não importado do
# model) porque schemas/ nunca depende de models/ (ver DECISÃO no
# README: schemas são o contrato público, models é estrutura interna).
_VALID_ROLES = ("owner", "admin", "financeiro", "atendimento", "auditor")


def _validate_role(v: str) -> str:
    if v not in _VALID_ROLES:
        raise ValueError(f"role deve ser um de: {', '.join(_VALID_ROLES)}")
    return v


class UserCreateRequest(BaseModel):
    """Sem tenant_id nem password (senha temporária é gerada pelo servidor
    — ver DECISÃO em app/services/user_service.py — nunca escolhida pelo
    admin que cria o usuário, para não reintroduzir senhas fracas/repetidas
    "digitadas de cabeça")."""

    email: EmailStr
    full_name: str
    role: str = "atendimento"

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        return _validate_role(v)


class UserUpdateRequest(BaseModel):
    """Todos os campos opcionais — PATCH parcial. is_active=false é a forma
    de "desligar" um colaborador sem apagar histórico (audit_log e billing
    referenciam users.id)."""

    full_name: str | None = None
    role: str | None = None
    is_active: bool | None = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str | None) -> str | None:
        return _validate_role(v) if v is not None else v


class UserResponse(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class PasswordChangeRequest(BaseModel):
    """Self-service — o próprio usuário autenticado troca a própria senha,
    precisa provar que conhece a senha atual (não é o admin resetando)."""

    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def enforce_minimum_strength(cls, v: str) -> str:
        return validate_password_strength(v)


class PasswordResetResponse(BaseModel):
    """Devolvida UMA ÚNICA VEZ, na resposta do reset administrado — a senha
    temporária nunca é persistida em texto puro nem reexibida depois
    (mesma lógica de PasswordResetResponse.temporary_password de uma API
    key: mostrado uma vez, esquecido em seguida)."""

    temporary_password: str
    must_change_password: bool = True
