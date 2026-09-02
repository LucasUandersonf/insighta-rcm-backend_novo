"""
app/api/v1/endpoints/auth.py

Único endpoint do sistema que usa get_db_no_tenant (sessão sem RLS
"aberto") em vez de DbSession — porque, no momento do login, ainda não
existe tenant conhecido. A resolução de credenciais passa pela função
SECURITY DEFINER (core.resolve_login), acessada via AuthRepository, que
é o único ponto autorizado a ler cross-tenant. Depois de autenticado, o
tenant_id resolvido vai DENTRO do JWT, e todas as requisições seguintes
usam a DbSession normal (tenant-aware).

CORREÇÃO (Auditoria Go-Live, achado F-04) — e-mail duplicado entre
tenants deixou de escolher um tenant arbitrário
-------------------------------------------------------------------------
O schema permite (de propósito — consultores atendem múltiplas clínicas)
o mesmo e-mail existir em mais de um tenant. Antes, `resolve_login`
devolvia só o primeiro registro que o Postgres encontrasse — não
determinístico, e o usuário nunca escolhia qual clínica queria acessar.

Fluxo novo:
  1) Busca TODOS os candidatos daquele e-mail (AuthRepository.resolve_login_candidates).
  2) Verifica a senha contra CADA candidato — mais de um pode bater
     (mesma senha reaproveitada em duas clínicas é comum e legítimo).
  3) Descarta candidatos com usuário/tenant desativado da lista de
     "usáveis" (mas ainda usa a lista completa para decidir a mensagem
     de erro certa, igual ao comportamento anterior).
  4) 0 usáveis -> erro genérico (mesmo texto de sempre, sem enumeração).
  5) 1 usável -> login direto, sem mudança de comportamento perceptível
     para a esmagadora maioria dos usuários (só têm 1 tenant).
  6) >1 usáveis:
     - se `tenant_id` já veio no payload e bate com um dos usáveis ->
       login direto nesse tenant.
     - senão -> devolve `requires_tenant_selection=True` com a lista de
       tenants (id + nome fantasia) para o frontend perguntar qual
       clínica, SEM emitir token nenhum ainda.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.core.security import create_access_token, verify_password
from app.db.session import get_db_no_tenant
from app.repositories.auth_repository import AuthRepository, LoginRecord
from app.schemas.token import (
    LoginRequest,
    LoginResponse,
    PasswordResetConfirmRequest,
    PasswordResetRequestRequest,
    RegisterRequest,
    TenantOption,
    TokenResponse,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()

_GENERIC_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="E-mail ou senha inválidos.",
)
_DEACTIVATED_ERROR = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Usuário ou clínica desativados. Contate o suporte.",
)


@router.post("/login", response_model=LoginResponse)
@limiter.limit(settings.LOGIN_RATE_LIMIT)
async def login(
    request: Request,  # injetado pelo slowapi; ver nota no main.py
    credentials: LoginRequest,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> LoginResponse:
    repo = AuthRepository(db)
    candidates = await repo.resolve_login_candidates(credentials.email)

    # Mensagem de erro IDÊNTICA para "e-mail não existe" e "senha errada"
    # em qualquer um dos tenants candidatos. Diferenciar dá a um
    # atacante uma forma de enumerar e-mails válidos no sistema (user
    # enumeration) — inaceitável em HealthTech, onde a lista de clientes
    # de uma clínica já é informação sensível.
    password_matches: list[LoginRecord] = [c for c in candidates if verify_password(credentials.password, c.hashed_password)]

    if not password_matches:
        raise _GENERIC_ERROR

    usable = [c for c in password_matches if c.is_active and c.tenant_is_active]

    if not usable:
        # Senha bateu em pelo menos um tenant, mas todos os que bateram
        # estão desativados. Aqui SIM diferenciamos a mensagem, pois
        # "conta desativada" não é informação sigilosa da mesma forma
        # que "e-mail não cadastrado".
        raise _DEACTIVATED_ERROR

    if len(usable) > 1:
        if credentials.tenant_id is not None:
            chosen = next((c for c in usable if c.tenant_id == credentials.tenant_id), None)
            if chosen is None:
                # tenant_id não corresponde a nenhum candidato com senha
                # válida — tratado como credencial inválida, não como
                # "tenant não encontrado" (evitaria enumeração de tenant_id).
                raise _GENERIC_ERROR
            return _issue_token(chosen)

        return LoginResponse(
            requires_tenant_selection=True,
            tenant_options=[TenantOption(tenant_id=c.tenant_id, trade_name=c.tenant_trade_name) for c in usable],
        )

    return _issue_token(usable[0])


def _issue_token(record: LoginRecord) -> LoginResponse:
    token = create_access_token(user_id=record.user_id, tenant_id=record.tenant_id, role=record.role)
    return LoginResponse(access_token=token)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.REGISTER_RATE_LIMIT)
async def register(
    request: Request,  # injetado pelo slowapi; ver nota no main.py
    payload: RegisterRequest,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> TokenResponse:
    """Cadastro público (self-signup) — modelo SaaS confirmado com o
    usuário: quem se cadastra e escolhe um plano já vira owner da própria
    clínica, autenticado imediatamente (sem etapa de verificação de
    e-mail nesta primeira versão — ver DECISÃO em RegisterRequest). A
    cobrança de verdade do plano escolhido fica para uma etapa seguinte."""
    return await AuthService(db).register(payload)


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit(settings.PASSWORD_RESET_RATE_LIMIT)
async def request_password_reset(
    request: Request,
    payload: PasswordResetRequestRequest,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> None:
    """SEMPRE devolve 202 sem corpo, exista ou não o e-mail — mesmo
    princípio anti-enumeração do login (ver módulo acima): a resposta não
    pode ajudar um atacante a descobrir quais e-mails têm conta no
    sistema. O envio de fato (ou não) acontece dentro de AuthService,
    silenciosamente."""
    await AuthService(db).request_password_reset(payload.email)


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(settings.PASSWORD_RESET_RATE_LIMIT)
async def confirm_password_reset(
    request: Request,
    payload: PasswordResetConfirmRequest,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> None:
    await AuthService(db).confirm_password_reset(payload.token, payload.new_password)
