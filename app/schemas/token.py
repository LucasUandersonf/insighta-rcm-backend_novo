"""
DECISÃO — Por que Pydantic schemas são a defesa contra Mass Assignment
-------------------------------------------------------------------------
Nunca aceitamos um SQLAlchemy model diretamente como corpo de requisição.
Cada endpoint declara um schema de ENTRADA explícito (ex: LoginRequest)
contendo SOMENTE os campos que o cliente tem permissão de enviar. Mesmo
que alguém injete `{"role": "owner", "tenant_id": "..."}` no corpo do
JSON de login, o Pydantic simplesmente ignora/rejeita campos não
declarados no schema — é estruturalmente impossível "promover" a própria
role ou trocar de tenant via payload.

CORREÇÃO (Auditoria Go-Live, achado F-04) — LoginRequest ganhou
`tenant_id` OPCIONAL: só é usado para desempatar quando o mesmo e-mail
tem senha válida em mais de um tenant (consultor multi-clínica). Não é
mass assignment porque o backend NUNCA confia nesse campo sozinho — ele
só é aceito se bater com um dos tenants para os quais a senha enviada
JÁ foi validada (ver app/api/v1/endpoints/auth.py). Enviar um tenant_id
arbitrário sem a senha correspondente continua resultando em erro
genérico, exatamente como antes.
"""
from pydantic import BaseModel, EmailStr, field_validator

from app.core.security import validate_password_strength
from app.schemas.tenant import AVAILABLE_PLAN_TIERS


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    tenant_id: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TenantOption(BaseModel):
    tenant_id: str
    trade_name: str


class LoginResponse(BaseModel):
    """
    Substitui TokenResponse como response_model de POST /auth/login.
    Compatível por extensão: quando não há ambiguidade de tenant, a
    resposta tem exatamente os mesmos campos que TokenResponse sempre
    teve (access_token + token_type), então nenhum cliente existente
    quebra. `requires_tenant_selection=True` é o único caso novo: o
    frontend precisa reconhecer esse campo e reenviar o login com
    `tenant_id` preenchido (ver LoginPage.tsx).
    """
    access_token: str | None = None
    token_type: str = "bearer"
    requires_tenant_selection: bool = False
    tenant_options: list[TenantOption] = []


# --- Cadastro público (self-signup) ---
class RegisterRequest(BaseModel):
    """POST /auth/register — cria a clínica (tenant) + o primeiro usuário,
    sempre com role "owner" (nunca aceito do cliente — mesmo princípio de
    mass assignment do topo deste arquivo: quem se cadastra sempre vira
    dono da própria clínica, nunca escolhe o próprio papel).

    Modelo confirmado com o usuário: cadastro + escolha de plano acontecem
    já nesta chamada; a cobrança de verdade (gateway de pagamento) fica
    para uma etapa seguinte — plan_tier aqui só registra a intenção,
    tenant nasce ativo (Tenant.is_active=True), sem estado de "pendente
    de pagamento" (não existe hoje um model de assinatura/cobrança neste
    backend para sustentar esse estado)."""

    trade_name: str
    legal_name: str | None = None  # None = usa trade_name (clínica pequena, sem razão social distinta)
    cnpj: str
    plan_tier: str = "starter"
    owner_name: str
    email: EmailStr
    password: str

    @field_validator("cnpj")
    @classmethod
    def validate_cnpj_format(cls, v: str) -> str:
        digits = "".join(ch for ch in v if ch.isdigit())
        if len(digits) != 14:
            raise ValueError("CNPJ inválido — informe os 14 dígitos (com ou sem pontuação).")
        return v

    @field_validator("plan_tier")
    @classmethod
    def validate_plan_tier(cls, v: str) -> str:
        if v not in AVAILABLE_PLAN_TIERS:
            raise ValueError(f"plan_tier deve ser um de: {', '.join(AVAILABLE_PLAN_TIERS)}")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return validate_password_strength(v)


# --- Recuperação de senha (self-service) ---
class PasswordResetRequestRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return validate_password_strength(v)
