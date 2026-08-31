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
from pydantic import BaseModel, EmailStr


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
