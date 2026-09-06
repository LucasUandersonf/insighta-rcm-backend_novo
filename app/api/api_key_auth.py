"""
app/api/api_key_auth.py

Autenticação por API key — para endpoints de INTEGRAÇÃO chamados por um
sistema do próprio cliente (ERP, CRM, planilha automatizada, Zapier/Make)
em vez de um usuário logado pelo frontend. Alternativa ao JWT de
app/api/deps.py: um JWT expira rápido e exige um fluxo de login humano,
o que não faz sentido para um script/integração que roda sozinho,
periodicamente, sem ninguém "logado" no momento da chamada.

BUG CORRIGIDO — a Central de Integrações emitia chaves que NUNCA eram
verificadas em lugar nenhum
-------------------------------------------------------------------------
`POST /integrations/api-keys` (ver app/sql/006_platform_admin.sql) já
existia desde o início do produto, e `ApiKeyService.verify_raw_key_against_hash`
já existia com o comentário "usado pelo endpoint de ingestão (futuro)" —
mas nenhum endpoint jamais chamava essa verificação. Um cliente podia
gerar uma chave de API na tela de Setup e ela não servia para autenticar
absolutamente nada. Este módulo + POST /integrations/ingest (ver
app/api/v1/endpoints/integrations.py) são a peça que faltava.

DECISÃO — mesmo problema de "ovo e galinha" do login, mesma solução
-------------------------------------------------------------------------
Quem chama com uma API key ainda não informa (e a aplicação ainda não
sabe) de qual tenant ela é — `core.api_keys` tem RLS normal, então sem
tenant setado nenhuma linha é visível. Resolvido com a MESMA técnica de
app/api/deps.py::get_current_user (JWT) e do fluxo de login/recuperação
de senha: uma função SQL SECURITY DEFINER
(`core.resolve_api_key_candidates`, ver app/sql/024_api_key_resolver.sql)
resolve os candidatos por `key_prefix` (não sensível, guardado em claro)
usando uma sessão SEM tenant (`get_db_no_tenant`); a aplicação então
verifica o hash de verdade (`verify_password`) candidato a candidato,
exatamente como o login faz com senha.
"""
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_password
from app.db.session import get_db_no_tenant, get_db_with_tenant
from app.repositories.api_key_repository import ApiKeyRepository

# Tamanho de generate_api_key()'s key_prefix (app/core/security.py) —
# uma chave mais curta que isso não pode ser válida, nem vale a pena
# tentar resolver.
_PREFIX_LENGTH = 12


class ApiKeyIdentity:
    """Equivalente a CurrentUser (app/api/deps.py) para chamadas
    autenticadas por API key — sem user_id/role, porque não há usuário
    logado: é uma credencial de MÁQUINA, associada a um tenant."""

    def __init__(self, tenant_id: str, api_key_id: str):
        self.tenant_id = tenant_id
        self.api_key_id = api_key_id


async def get_identity_from_api_key(x_api_key: Annotated[str | None, Header()] = None) -> ApiKeyIdentity:
    """
    Header dedicado (`X-API-Key`), não `Authorization: Bearer` — de
    propósito: evita qualquer ambiguidade com o esquema JWT já usado por
    app/api/deps.py (um cliente de integração nunca precisa mandar os
    dois no mesmo endpoint) e deixa a integração do cliente auto-
    explicativa ("manda sua chave no header X-API-Key").
    """
    if not x_api_key or len(x_api_key) < _PREFIX_LENGTH:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Chave de API ausente ou inválida.")

    prefix = x_api_key[:_PREFIX_LENGTH]
    async for session in get_db_no_tenant():
        candidates = await ApiKeyRepository(session).find_candidates_by_prefix(prefix)
        for candidate in candidates:
            if candidate.revoked_at is not None:
                continue
            if verify_password(x_api_key, candidate.key_hash):
                return ApiKeyIdentity(tenant_id=str(candidate.tenant_id), api_key_id=str(candidate.id))

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Chave de API inválida ou revogada.")


ApiKeyIdentityDep = Annotated[ApiKeyIdentity, Depends(get_identity_from_api_key)]


async def get_db_via_api_key(identity: ApiKeyIdentityDep) -> AsyncGenerator[AsyncSession, None]:
    """Mesmo papel de app/api/deps.py::get_db, mas a partir de uma
    ApiKeyIdentity em vez de um CurrentUser — abre a sessão tenant-aware
    só DEPOIS que a chave já foi verificada."""
    async for session in get_db_with_tenant(identity.tenant_id):
        yield session


ApiKeyDbSession = Annotated[AsyncSession, Depends(get_db_via_api_key)]
