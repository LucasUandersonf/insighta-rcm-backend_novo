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
from pydantic import BaseModel, EmailStr, field_validator, model_validator

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

    # Duas formas de completar a identidade do owner — exatamente UMA
    # das duas precisa vir preenchida (ver validate_auth_method abaixo):
    # 1) owner_name + email + password (cadastro tradicional).
    # 2) google_credential (cadastro via "Continuar com Google" — ver
    #    app/services/google_oauth_client.py): o backend RE-VERIFICA este
    #    ID token e deriva nome/e-mail DELE, nunca de owner_name/email
    #    enviados pelo cliente — por isso os dois são mutuamente
    #    exclusivos, não só "um substitui o outro na prática".
    owner_name: str | None = None
    email: EmailStr | None = None
    password: str | None = None
    google_credential: str | None = None

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

    @model_validator(mode="after")
    def validate_auth_method(self) -> "RegisterRequest":
        if self.google_credential:
            if self.owner_name is not None or self.email is not None or self.password is not None:
                raise ValueError(
                    "Cadastro via Google não usa owner_name/email/password — esses dados vêm do próprio Google."
                )
        else:
            if not self.owner_name or not self.email or not self.password:
                raise ValueError("Informe owner_name, email e password, ou cadastre-se com google_credential.")
            validate_password_strength(self.password)
        return self


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


# --- Login com Google ("Sign in with Google") ---
class GoogleCredentialRequest(BaseModel):
    """`credential` é o ID token que o Google Identity Services entrega ao
    CALLBACK do botão no frontend — nunca uma senha nem um código de
    autorização. `tenant_id` segue o mesmo papel de LoginRequest.tenant_id:
    só usado para desempatar quando o e-mail da conta Google tem contas
    ativas em mais de uma clínica."""

    credential: str
    tenant_id: str | None = None


class GoogleAuthResponse(BaseModel):
    """POST /auth/google pode terminar em 3 estados bem diferentes — os
    3 cabem neste único schema (campos default vazios/None nos que não
    se aplicam) para o frontend não precisar tratar formatos de resposta
    diferentes por branch de negócio, só checar os campos presentes:

    1) Conta já existe (e sem ambiguidade de tenant) -> access_token.
    2) Conta já existe em mais de uma clínica -> requires_tenant_selection
       + tenant_options (mesmo padrão de LoginResponse).
    3) Nenhuma conta com este e-mail -> needs_registration=True + email/
       suggested_owner_name, para o frontend pré-preencher o cadastro
       (ver SignUpPage.tsx) sem pedir para digitar nome/e-mail de novo.
    """

    access_token: str | None = None
    token_type: str = "bearer"
    requires_tenant_selection: bool = False
    tenant_options: list[TenantOption] = []
    needs_registration: bool = False
    email: str | None = None
    suggested_owner_name: str | None = None
