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
`create_platform_admin_token` em app/core/security.py) que carrega só a
claim `scope`, nunca `tenant_id`/`role`.

Autenticação por SENHA ÚNICA compartilhada (não por usuário/senha
individual) é uma escolha deliberada de escopo para a v1: hoje a equipe
Insighta é pequena, e criar uma tabela de "usuários da plataforma" com
CRUD, RBAC próprio etc. seria a obra da opção "login próprio da equipe"
que foi avaliada e adiada nesta rodada (ver conversa que motivou esta
frente) — evolução natural se a equipe/uso interno crescer.
"""
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


def get_platform_admin(token: Annotated[str, Depends(oauth2_scheme_platform)]) -> None:
    """
    Dependency de rota para todo endpoint de /platform (exceto o próprio
    /platform/login). Não devolve um "usuário" — não existe um; só valida
    que o token é um JWT genuíno, não expirado, com `scope == "platform_admin"`.
    Rejeita explicitamente um JWT de usuário de clínica que por acaso
    chegue aqui (teria `scope` ausente, nunca "platform_admin").
    """
    try:
        payload: dict[str, Any] = decode_access_token(token)
    except JWTError as exc:
        raise _INVALID_TOKEN_ERROR from exc

    if payload.get("scope") != "platform_admin":
        raise _INVALID_TOKEN_ERROR


PlatformAdminDep = Depends(get_platform_admin)
