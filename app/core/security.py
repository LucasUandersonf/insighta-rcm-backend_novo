"""
app/core/security.py

Responsabilidades:
  1) Hash e verificação de senha (argon2 — vencedor da Password Hashing
     Competition, mais resistente a ataques de GPU do que bcrypt puro).
  2) Emissão e decodificação de JWT.

DECISÃO — O que colocamos DENTRO do JWT
-----------------------------------------------------------------------
O payload do token carrega `sub` (user_id), `tenant_id` e `role`. Isso é
o que permite ao middleware/dependency de tenant (app/api/deps.py) montar
o contexto RLS SEM precisar consultar o banco de novo a cada request só
para descobrir "de qual clínica é esse usuário". O trade-off consciente:
se um usuário mudar de tenant ou tiver o role alterado, isso só reflete
em um novo token (temos expiração curta de 30 min por padrão + refresh
token para mitigar). Não colocamos nada sensível (senha, CPF) no payload,
pois o JWT é apenas assinado, não criptografado — qualquer um pode
decodificar o payload (base64), só não pode forjar a assinatura.
"""
from datetime import datetime, timedelta, timezone
from typing import Any
import hashlib
import hmac
import secrets

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

settings = get_settings()

# CryptContext com argon2 como esquema principal; mantemos bcrypt como
# "deprecated=auto" apenas para permitir migração de hashes antigos caso
# o sistema já tenha nascido com bcrypt em algum ambiente legado.
pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(*, user_id: str, tenant_id: str, role: str) -> str:
    """
    Gera o JWT que o cliente (frontend) enviará em cada requisição via
    header Authorization: Bearer <token>.
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload: dict[str, Any] = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Lança jose.JWTError se o token for inválido, expirado ou tiver
    assinatura incorreta. O chamador (app/api/deps.py) converte isso em
    HTTP 401.
    """
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def generate_temporary_password() -> str:
    """Usada pela Gestão de Usuários (criação de conta / reset administrado
    por admin ou owner) — nunca pelo próprio usuário. `secrets.token_urlsafe`
    (CSPRNG, não `random`) gera 16 bytes -> ~22 caracteres, entropia bem
    acima do mínimo de 8 caracteres que exigimos de senhas escolhidas por
    humanos (ver PasswordChangeRequest em app/schemas/user.py)."""
    return secrets.token_urlsafe(16)


def generate_api_key() -> tuple[str, str]:
    """Gera uma chave de API para a Central de Integrações & Webhooks.
    Retorna (chave_em_texto_puro, prefixo). A chave completa só existe
    neste retorno — o chamador (ApiKeyService) faz hash_password() nela
    antes de persistir, exatamente como uma senha nunca é guardada em
    texto puro. O prefixo (8 caracteres, não sensível) permite ao cliente
    reconhecer "qual chave é essa" numa listagem sem reexibir o segredo."""
    raw = f"iarcm_{secrets.token_hex(24)}"
    return raw, raw[:12]


def verify_meta_webhook_signature(*, payload: bytes, signature_header: str | None, secret: str) -> bool:
    """
    Verifica o header X-Hub-Signature-256 que a Meta envia em todo
    webhook: sha256=<hmac hex do corpo cru, usando o app secret>.

    CRÍTICO: `payload` precisa ser os BYTES CRUS do corpo da requisição,
    exatamente como recebidos — nunca o resultado de re-serializar o JSON
    já parseado. Reserializar pode mudar espaçamento/ordem de chaves e
    fazer a assinatura não bater mesmo com um payload legítimo. Por isso
    o endpoint (app/api/v1/endpoints/webhooks.py) lê `await request.body()`
    ANTES de qualquer parsing, e só then passa os bytes para cá.

    Usamos hmac.compare_digest (comparação em tempo constante) em vez de
    `==` para não vazar, por timing, quantos bytes da assinatura estão
    corretos — mitigação padrão contra timing attack em comparação de HMAC.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    received = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, received)
