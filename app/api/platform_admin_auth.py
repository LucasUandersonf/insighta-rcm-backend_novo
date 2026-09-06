"""
app/api/platform_admin_auth.py

Autenticação do painel interno de Customer Success — a EQUIPE que opera
a Insighta (não um usuário de clínica) logando para ver uso agregado por
tenant. Ver DECISÃO completa em app/sql/026_platform_customer_success.sql
sobre por que este é um mecanismo TOTALMENTE separado do login de
clínica (app/api/deps.py), não uma extensão dele.

POR QUE NÃO REAPROVEITAR O JWT/RBAC DE USUÁRIO DE CLÍNICA
-------------------------------------------------------------------------
Todo papel que existe hoje (`owner`, `admin`, `financeiro`, `atendimento`,
`auditor`) é um papel DENTRO de um tenant — o isolamento por RLS garante
que mesmo o `owner` mais poderoso nunca vê nada fora do próprio tenant.
"Customer Success orientado a dados" é o oposto por definição: uma visão
que atravessa TODOS os tenants ao mesmo tempo, de propósito. Se esse dado
fosse acessível por qualquer papel de clínica, cada cliente enxergaria o
quanto os OUTROS clientes usam o produto — vazamento de informação
comercial grave. Por isso este módulo não estende `get_current_user`;
é um credencial e um fluxo à parte, com seu próprio JWT (ver
`create_platform_admin_token` em app/core/security.py) que carrega
`sub` (id de core.platform_users) + a claim `scope`, nunca `tenant_id`/
`role`.

DECISÃO — login individual (core.platform_users), não mais senha única
compartilhada
-------------------------------------------------------------------------
A v1 deste painel usava uma senha única compartilhada — escolha
deliberada de escopo para uma equipe pequena. Com mais de uma ação real
acontecendo aqui (ver POST /platform/alerts/run), passou a fazer sentido
saber QUEM fez o quê — core.platform_audit_log guarda isso. Sem RBAC
próprio ainda (todo platform_user pode tudo — ver DECISÃO em
app/sql/029_platform_users.sql): evolução natural se o time crescer.
"""
import uuid
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError

from app.core.security import decode_access_token

# tokenUrl separado do fluxo de clínica (oauth2_scheme em app/api/deps.py)
# — evita qualquer ambiguidade na doc /docs sobre "logar com o quê, onde".
oauth2_scheme_platform = OAuth2PasswordBearer(tokenUrl="/api/v1/platform/login")

_INVALID_TOKEN_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Sessão inválida ou expirada.",
    headers={"WWW-Authenticate": "Bearer"},
)


class PlatformAdminIdentity:
    """Equivalente a CurrentUser (app/api/deps.py) para o painel interno
    — carrega só o id de core.platform_users, extraído de `sub`. Não tem
    tenant_id/role porque não existe tenant nem papel nesta sessão."""

    def __init__(self, platform_user_id: uuid.UUID):
        self.platform_user_id = platform_user_id


def get_platform_admin(token: Annotated[str, Depends(oauth2_scheme_platform)]) -> PlatformAdminIdentity:
    """
    Dependency de rota para todo endpoint de /platform (exceto o próprio
    /platform/login). Valida que o token é um JWT genuíno, não expirado,
    com `scope == "platform_admin"` — rejeita explicitamente um JWT de
    usuário de clínica que por acaso chegue aqui (teria `scope` ausente,
    nunca "platform_admin") — e devolve a identidade de quem está
    chamando, para os endpoints que precisam registrar QUEM fez uma ação
    (ver POST /platform/alerts/run).
    """
    try:
        payload: dict[str, Any] = decode_access_token(token)
    except JWTError as exc:
        raise _INVALID_TOKEN_ERROR from exc

    if payload.get("scope") != "platform_admin":
        raise _INVALID_TOKEN_ERROR

    try:
        platform_user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise _INVALID_TOKEN_ERROR from exc

    return PlatformAdminIdentity(platform_user_id=platform_user_id)


PlatformAdminDep = Annotated[PlatformAdminIdentity, Depends(get_platform_admin)]
