"""
app/main.py — ponto de entrada da aplicação.

O que NÃO está aqui, de propósito: nenhum middleware ASGI de TENANT.
Como explicado em app/db/session.py e app/api/deps.py, o contexto de
tenant é resolvido via cadeia de Depends() por requisição, não por um
middleware global — porque só assim garantimos que o SET LOCAL e as
queries do endpoint compartilham a mesma transação de banco.

O middleware de REQUEST ID/logging abaixo é diferente e não tem esse
problema: não toca no banco, só mede tempo e loga — ASGI middleware é
seguro para isso.
"""
import logging
import time

import sentry_sdk
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging_config import configure_logging
from app.core.rate_limit import limiter
from app.core.request_context import get_request_id, set_request_id
from app.db.session import get_db_no_tenant

settings = get_settings()
configure_logging(settings.ENVIRONMENT)
logger = logging.getLogger("app")

# =====================================================================
# SENTRY — monitoramento de erros (OPCIONAL)
# =====================================================================
# DECISÃO — inicializar cedo, guardado por SENTRY_DSN, sem registrar nada
# manualmente
# -------------------------------------------------------------------------
# Sem SENTRY_DSN configurada, este bloco não chama sentry_sdk.init()
# nenhuma vez — o SDK fica completamente inerte (as chamadas de
# sentry_sdk.capture_exception()/set_tag() feitas mais abaixo, nos
# handlers, viram no-op nesse caso). Isso segue exatamente o mesmo padrão
# de toda outra integração externa opcional deste projeto
# (ANTHROPIC_API_KEY, AWS_S3_CONTRACTS_BUCKET etc. em app/core/config.py):
# ausência de configuração = feature desligada, nunca crash no boot.
#
# A inicialização acontece ANTES de `FastAPI()` ser instanciado de
# propósito: o sentry_sdk detecta e integra automaticamente
# Starlette/FastAPI (via seus entry points, sem precisarmos registrar um
# middleware/integração manualmente) — mas essa integração automática só
# enxerga apps criados depois do init.
#
# send_default_pii=False é uma escolha explícita, não o padrão do SDK: o
# padrão do Sentry manda corpo de requisição, headers e dados de usuário
# (nome/e-mail) para a Sentry por padrão em algumas integrações. Este é um
# sistema de saúde (RCM médico) — dado de paciente/beneficiário não pode
# vazar para um serviço terceiro de monitoramento de erro, na mesma linha
# de cuidado com LGPD já presente no resto deste arquivo (nunca devolver
# stack trace/detalhe técnico ao cliente, nunca logar segredo). Contexto
# útil para suporte (request_id, tenant_id, role) é anexado manualmente,
# como TAG, nunca como corpo de request ou dado pessoal.
if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        profiles_sample_rate=settings.SENTRY_PROFILES_SAMPLE_RATE,
        send_default_pii=False,
    )

app = FastAPI(
    title="RCM/ERP Médico — API",
    version="0.1.0",
    # Em produção, docs/redoc costumam ser desligados ou protegidos por
    # rede interna — expor o schema completo da API publicamente facilita
    # reconhecimento para um atacante.
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url=None,
)

# --- Rate limiting global (DevSecOps: proteção contra abuso/DoS) ---
app.state.limiter = limiter

# --- CORS ---
# Em produção, allow_origins deve ser a lista explícita dos domínios do
# frontend (nunca "*" quando allow_credentials=True, sob risco de expor
# a API a qualquer site que o usuário logado visite).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =====================================================================
# MIDDLEWARE DE REQUEST ID + LOG DE ACESSO
# =====================================================================
# Todo request ganha um ID (reaproveitado de X-Request-ID se o cliente
# já mandou um — útil quando o frontend/gateway já gera um próprio).
# Esse ID: (1) volta no header da resposta, (2) aparece em toda linha
# de log gerada durante o request, (3) aparece no corpo de qualquer
# resposta de erro — fechando o ciclo "usuário reporta -> eu busco pelo
# ID -> vejo exatamente o que aconteceu", sem precisar de print de tela
# nem estimar horário.
@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = set_request_id(request.headers.get("X-Request-ID"))
    if settings.SENTRY_DSN:
        # Tag disponível em QUALQUER evento reportado durante este request
        # (não só em exceções não tratadas) — permite ir do request_id que
        # o usuário citou no suporte direto ao evento no Sentry. tenant_id
        # e role NÃO entram aqui: como documentado no topo deste arquivo e
        # em app/api/deps.py, CurrentUser só existe depois da cadeia de
        # Depends() do endpoint (JWT -> tenant_id), que roda depois deste
        # middleware — por isso essas duas tags são setadas em
        # get_current_user (app/api/deps.py) quando disponíveis, não aqui.
        sentry_sdk.set_tag("request_id", request_id)
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = round((time.monotonic() - start) * 1000, 1)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request_completed",
        extra={
            "path": request.url.path,
            "method": request.method,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    return response


# =====================================================================
# TRATAMENTO DE ERRO — DUAS AUDIÊNCIAS AO MESMO TEMPO
# =====================================================================
# DECISÃO — envelope de erro único e consistente para TODO erro da API
# -------------------------------------------------------------------------
# Toda resposta de erro (400 a 500) sai no mesmo formato:
#   {"error_code": "...", "message": "...", "request_id": "..."}
# Isso serve duas audiências diferentes com o MESMO mecanismo:
#   - Para o FRONTEND: `error_code` é uma chave estável para mapear em
#     texto/ícone de interface, sem depender de fazer parsing de string
#     de erro em português (que muda de frase e quebra a lógica do front).
#   - Para o USUÁRIO FINAL: `message` já vem em português, sem jargão
#     técnico, dizendo o que aconteceu — nunca uma stack trace ou
#     "Internal Server Error" cru. `request_id` é o número que ele pode
#     citar numa mensagem de suporte.
# Para o DESENVOLVEDOR (hoje, só eu): o traceback completo vai pro log
# estruturado (nunca pro cliente), correlacionável pelo mesmo request_id
# que apareceu na tela do usuário — dado sensível de erro nunca vaza pra
# fora, mas o diagnóstico completo está a uma busca de distância.
_ERROR_CODE_BY_STATUS = {
    400: "requisicao_invalida",
    401: "nao_autenticado",
    403: "sem_permissao",
    404: "nao_encontrado",
    409: "conflito",
    422: "dados_invalidos",
    429: "limite_excedido",
}

_FRIENDLY_MESSAGE_BY_STATUS = {
    401: "Sua sessão expirou ou é inválida. Faça login novamente.",
    403: "Você não tem permissão para fazer isso.",
    404: "Não encontramos o que você está procurando.",
    409: "Isso conflita com algo que já existe no sistema.",
    422: "Alguns dos dados enviados não são válidos. Confira os campos e tente de novo.",
    429: "Muitas tentativas em pouco tempo. Aguarde um instante e tente novamente.",
}


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    error_code = _ERROR_CODE_BY_STATUS.get(exc.status_code, "erro")
    message = _FRIENDLY_MESSAGE_BY_STATUS.get(exc.status_code) or str(exc.detail)
    logger.warning("http_exception", extra={"status_code": exc.status_code})
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error_code": error_code,
            "message": message,
            "request_id": get_request_id(),
            # "detail" carrega o texto técnico original (ex: mensagens
            # específicas de auth.py) — o frontend pode ignorá-lo e usar
            # só "message", mas fica disponível para depuração.
            "detail": exc.detail,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Transforma o erro "cru" do Pydantic (JSON schema, em inglês, cheio
    # de jargão) numa lista simples de "campo -> o que está errado" —
    # o frontend consegue destacar o campo certo no formulário sem ter
    # que entender a estrutura interna do Pydantic.
    campos = [
        {"campo": ".".join(str(p) for p in err["loc"] if p != "body"), "problema": err["msg"]}
        for err in exc.errors()
    ]
    logger.warning("validation_error", extra={"path": request.url.path})
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error_code": "dados_invalidos",
            "message": "Alguns dos dados enviados não são válidos. Confira os campos e tente de novo.",
            "request_id": get_request_id(),
            "campos": campos,
        },
    )


@app.exception_handler(RateLimitExceeded)
async def rate_limit_exception_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    logger.warning("rate_limit_exceeded", extra={"path": request.url.path})
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "error_code": "limite_excedido",
            "message": "Muitas tentativas em pouco tempo. Aguarde um instante e tente novamente.",
            "request_id": get_request_id(),
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # ÚLTIMA linha de defesa: qualquer exceção que NENHUM outro handler
    # pegou. O traceback completo vai pro log (nível ERROR, com
    # exc_info) — é a única exceção sobre "nunca mandar detalhe técnico
    # pro cliente", porque aqui o detalhe é justamente o que o
    # desenvolvedor precisa e o usuário não deve ver.
    logger.error("unhandled_exception", exc_info=exc, extra={"path": request.url.path, "method": request.method})
    request_id = get_request_id()
    if settings.SENTRY_DSN:
        # DECISÃO — tenant_id/role só entram quando já resolvidos
        # -------------------------------------------------------------
        # request.state.current_user só existe se a exceção estourou DEPOIS
        # da dependency get_current_user já ter rodado (ver
        # app/api/deps.py) — ou seja, em rotas autenticadas, na maioria dos
        # casos. Em erro ANTES disso (ex: 500 num endpoint público, ou
        # numa dependency anterior), simplesmente não tem tenant/role pra
        # anexar ainda — e está certo não forçar isso artificialmente
        # (não vale abrir uma segunda decodificação de JWT só pra isso,
        # nem criar um middleware de tenant, que o topo deste arquivo já
        # explica por que não existe).
        current_user = getattr(request.state, "current_user", None)
        if current_user is not None:
            sentry_sdk.set_tag("tenant_id", current_user.tenant_id)
            sentry_sdk.set_tag("role", current_user.role)
        sentry_sdk.set_tag("request_id", request_id)
        sentry_sdk.capture_exception(exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error_code": "erro_interno",
            "message": (
                "Algo deu errado do nosso lado — não foi você. "
                f"Se o problema continuar, entre em contato informando o código {request_id}."
            ),
            "request_id": request_id,
        },
    )


app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/health", tags=["infra"])
async def health_check() -> dict[str, str]:
    """
    Diferente da versão anterior (que só respondia "ok" sem checar
    nada): agora testa uma conexão real com o banco. Um load balancer
    que só recebesse "ok" de um processo com banco fora do ar continuaria
    mandando tráfego pra ele — o health check precisa refletir a
    capacidade REAL de atender requisição, não só "o processo Python
    está vivo".
    """
    try:
        async for session in get_db_no_tenant():
            await session.execute(text("SELECT 1"))
            break
        return {"status": "ok", "database": "ok"}
    except Exception:
        logger.error("health_check_failed", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "degraded", "database": "unreachable"},
        )
