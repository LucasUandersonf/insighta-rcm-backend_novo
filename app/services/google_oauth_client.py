"""
app/services/google_oauth_client.py

Verificação de "Sign in with Google" via ID token (Google Identity
Services, https://accounts.google.com/gsi/client): o frontend nunca lida
com senha nem client_secret — o botão é renderizado pelo PRÓPRIO Google
e devolve um JWT já assinado ("credential") ao callback do frontend, que
manda esse token pra cá só para VERIFICAÇÃO. Por isso não existe
client_secret nenhum neste fluxo, só o client_id (usado para conferir a
claim "aud" do token) — ver GOOGLE_OAUTH_CLIENT_ID em app/core/config.py.

DECISÃO — verificação manual via JWKS + python-jose, não o SDK google-auth
-------------------------------------------------------------------------
Mesmo raciocínio de app/services/email_client.py (SMTP genérico em vez de
SDK de provedor específico): `python-jose` já é dependência deste projeto
(usado para os PRÓPRIOS JWTs, ver app/core/security.py) e já sabe
verificar RS256 a partir de uma chave em formato JWK; `httpx` também já é
dependência (usado pelo whatsapp_client.py). Puxar o pacote `google-auth`
só reimplementaria, com outra biblioteca, o que estas duas já fazem juntas.
"""
import time

import httpx
from fastapi import HTTPException, status
from jose import JWTError, jwt

from app.core.config import get_settings

settings = get_settings()

_GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
# O Google usa as duas formas como "iss" dependendo da versão do token —
# aceitar as duas é o comportamento documentado oficialmente pelo Google.
_GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
# Google rotaciona as chaves de quando em quando (não com frequência) —
# cache de 1h é conservador o bastante para não bater na API a cada
# login, e o fallback abaixo (refresh forçado se o "kid" não bate)
# cobre uma rotação acontecendo NO MEIO do cache ainda válido.
_JWKS_CACHE_TTL_SECONDS = 3600

_jwks_cache: dict[str, dict] = {}
_jwks_cache_fetched_at: float = 0.0

_INVALID_TOKEN_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Não foi possível validar sua conta Google. Tente novamente.",
)


class GoogleUserInfo:
    def __init__(self, email: str, name: str):
        self.email = email
        self.name = name


async def _get_google_jwks(*, force_refresh: bool = False) -> dict[str, dict]:
    global _jwks_cache, _jwks_cache_fetched_at
    if not force_refresh and _jwks_cache and (time.monotonic() - _jwks_cache_fetched_at) < _JWKS_CACHE_TTL_SECONDS:
        return _jwks_cache

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(_GOOGLE_CERTS_URL)
        response.raise_for_status()

    _jwks_cache = {key["kid"]: key for key in response.json()["keys"]}
    _jwks_cache_fetched_at = time.monotonic()
    return _jwks_cache


async def verify_google_id_token(credential: str) -> GoogleUserInfo:
    """Verifica assinatura, emissor e audiência do ID token — levanta
    HTTPException (401/503) em qualquer falha. Só retorna e-mail/nome
    quando o Google confirma `email_verified=true`: um Google Account com
    e-mail não verificado não é prova suficiente de posse daquele e-mail."""
    if not settings.GOOGLE_OAUTH_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Login com Google não está disponível neste momento.",
        )

    try:
        unverified_header = jwt.get_unverified_header(credential)
    except JWTError:
        raise _INVALID_TOKEN_ERROR

    kid = unverified_header.get("kid")
    jwks = await _get_google_jwks()
    jwk = jwks.get(kid)
    if jwk is None:
        # "kid" pode ter rotacionado desde o último cache — uma tentativa
        # de refresh forçado antes de desistir de vez.
        jwks = await _get_google_jwks(force_refresh=True)
        jwk = jwks.get(kid)
        if jwk is None:
            raise _INVALID_TOKEN_ERROR

    try:
        payload = jwt.decode(credential, jwk, algorithms=["RS256"], audience=settings.GOOGLE_OAUTH_CLIENT_ID)
    except JWTError:
        raise _INVALID_TOKEN_ERROR

    if payload.get("iss") not in _GOOGLE_ISSUERS:
        raise _INVALID_TOKEN_ERROR

    if not payload.get("email_verified"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sua conta Google precisa ter o e-mail verificado para continuar.",
        )

    email = payload.get("email")
    if not email:
        raise _INVALID_TOKEN_ERROR

    name = payload.get("name") or email.split("@")[0]
    return GoogleUserInfo(email=email, name=name)
