"""
app/api/deps.py

Este arquivo é a "ponte viva" mencionada: encadeia JWT -> tenant_id ->
SET LOCAL -> sessão de banco pronta para o endpoint usar.

FLUXO COMPLETO DE UMA REQUISIÇÃO AUTENTICADA
-------------------------------------------------------------------------
1. Cliente envia  Authorization: Bearer <jwt>
2. get_current_token_payload() decodifica e valida assinatura/expiração.
3. get_current_tenant_id() extrai `tenant_id` do payload (já confiável,
   pois veio de um JWT assinado pelo servidor — nunca de um header ou
   query param que o cliente poderia forjar livremente).
4. get_db() (a dependency "tenant-aware") abre uma sessão, executa
   SET LOCAL app.current_tenant = tenant_id NA MESMA transação, e só
   então entrega a sessão para o repositório/service do endpoint.
5. Toda query feita a partir daqui, nesta sessão, já está automaticamente
   filtrada pelo RLS do Postgres.

Por que extrair tenant_id do JWT e não de um header customizado
(ex: X-Tenant-Id) enviado pelo cliente?
Porque um header é um dado não confiável — qualquer cliente HTTP pode
mandar X-Tenant-Id: <uuid-de-outra-clinica>. Usar o valor cravado dentro
do JWT assinado é o que impede um usuário autenticado da Clínica A de
simplesmente pedir para "ver" dados como se fosse a Clínica B.
-------------------------------------------------------------------------
"""
from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.session import get_db_with_tenant

# tokenUrl aponta para o endpoint de login (usado só para gerar a doc /docs)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_current_token_payload(
    token: Annotated[str, Depends(oauth2_scheme)],
) -> dict[str, Any]:
    """
    Decodifica e valida o JWT. Levanta 401 para token ausente, expirado
    ou com assinatura inválida — nunca revela QUAL desses três motivos
    ao cliente (evita dar pistas úteis a um atacante tentando enumerar
    tokens válidos).
    """
    try:
        return decode_access_token(token)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais inválidas ou expiradas.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


TokenPayload = Annotated[dict[str, Any], Depends(get_current_token_payload)]


class CurrentUser:
    """Objeto leve com os dados do usuário já extraídos do JWT validado."""

    def __init__(self, payload: dict[str, Any]):
        self.id: str = payload["sub"]
        self.tenant_id: str = payload["tenant_id"]
        self.role: str = payload["role"]


def get_current_user(payload: TokenPayload, request: Request) -> CurrentUser:
    # DECISÃO — guardar em request.state.current_user (não um middleware)
    # -------------------------------------------------------------------
    # Isso NÃO é o middleware de tenant que a docstring deste módulo/
    # app/main.py explica por que não existe: não faz nenhuma query nem
    # abre sessão de banco, só guarda um objeto Python já calculado no
    # escopo do request. Serve para app/main.py (unhandled_exception_handler)
    # conseguir anexar tenant_id/role como tag do Sentry quando a exceção
    # acontece DEPOIS desta dependency já ter rodado — sem duplicar a
    # decodificação do JWT lá.
    user = CurrentUser(payload)
    request.state.current_user = user
    return user


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


async def get_db(current_user: CurrentUserDep) -> AsyncGenerator[AsyncSession, None]:
    """
    Esta é a dependency que os endpoints "de negócio" (billing, patients,
    appointments etc.) devem usar. Ela depende de get_current_user, então
    o FastAPI garante a ordem: primeiro valida o JWT e extrai o tenant,
    só depois abre a sessão de banco já com o SET LOCAL aplicado.

    Nenhum endpoint de negócio pode acidentalmente "esquecer" de setar o
    tenant, porque a própria assinatura da dependency já exige um
    CurrentUser válido para existir.
    """
    async for session in get_db_with_tenant(current_user.tenant_id):
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db)]


def require_role(*allowed_roles: str):
    """
    Factory de dependency para RBAC em nível de rota.
    Uso no endpoint:  Depends(require_role("owner", "financeiro"))

    Nota de design: RBAC aqui é a SEGUNDA camada de defesa (defesa em
    profundidade) — mesmo que uma verificação de role falhe por bug de
    aplicação, o RLS (primeira camada, no banco) continua garantindo que
    o usuário, na pior hipótese, só veria dados do PRÓPRIO tenant, nunca
    de outra clínica. RBAC decide "o que" dentro do tenant; RLS decide
    "de qual tenant".
    """

    def dependency(current_user: CurrentUserDep) -> CurrentUser:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Você não tem permissão para executar esta ação.",
            )
        return current_user

    return dependency
