"""
app/db/session.py

DECISÃO ARQUITETURAL CENTRAL DESTE MÓDULO
=========================================================================
Por que o "SET LOCAL app.current_tenant" NÃO pode viver em um middleware
ASGI comum (@app.middleware("http")):
-------------------------------------------------------------------------
Um middleware ASGI roda ANTES da rota resolver suas dependências. Ele não
tem, de forma nativa, acesso à MESMA conexão/transação de banco que os
repositórios usarão depois — a sessão do SQLAlchemy normalmente só é
aberta dentro de uma Depends() na própria rota. Se o middleware abrisse
uma conexão só para rodar o SET LOCAL e a fechasse, o SET LOCAL seria
perdido (ele vale apenas para a transação em que foi executado) e a
query real, rodando em outra conexão do pool, NÃO veria o tenant setado
-> RLS bloquearia tudo (fail-closed) ou, pior, se mal configurado, um
tenant errado vazaria de uma conexão reciclada.

A solução correta é tratar isso como uma DEPENDENCY FASTAPI encadeada
(não um middleware), porque Depends() garante que o mesmo objeto
AsyncSession seja compartilhado entre "setar o tenant" e "rodar as
queries do endpoint" — ambos populam a MESMA transação. Chamamos isso
informalmente de "middleware de tenant", mas tecnicamente é uma dependency
de banco tenant-aware (get_db). Ver app/api/deps.py para o encadeamento
completo: get_current_user -> get_db (este arquivo).
=========================================================================
"""
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()

# echo=False em produção (settings.ENVIRONMENT controla isso se desejado).
# pool_size/max_overflow vêm de env porque o dimensionamento correto do
# pool depende do plano de infra (nº de workers uvicorn x conexões do RDS).
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,  # detecta conexões mortas (ex: RDS failover) antes de usá-las
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # evita lazy-load acidental após commit em contexto async
    autoflush=False,
)


async def get_db_no_tenant() -> AsyncGenerator[AsyncSession, None]:
    """
    Sessão "crua", SEM contexto de tenant setado.
    Uso extremamente restrito: apenas para o fluxo de login (antes de
    sabermos o tenant do usuário) e para rotas de administração da
    plataforma (fora do escopo deste skeleton). Qualquer query aqui está
    sujeita a RLS normalmente, ou seja, verá ZERO linhas nas tabelas
    tenant-scoped — é fail-closed por padrão, como deve ser.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_db_with_tenant(tenant_id: str) -> AsyncGenerator[AsyncSession, None]:
    """
    Sessão "tenant-aware": abre uma transação e, como PRIMEIRA instrução,
    executa `SET LOCAL app.current_tenant`. A partir daqui, toda query
    rodada nesta mesma `session` dentro deste `async with` enxerga apenas
    as linhas do tenant informado — é o Postgres, via RLS, quem garante
    isso, não uma cláusula WHERE escrita à mão em algum repositório.

    IMPORTANTE: usamos bind parameter (:tenant_id) e nunca f-string/
    concatenação aqui — SET LOCAL não aceita bind parameter nativamente
    em todos os drivers, então validamos o formato UUID ANTES de chegar
    aqui (ver app/api/deps.py, que só chama esta função com um tenant_id
    já validado/assinado pelo JWT, nunca com input bruto do usuário).
    Isso neutraliza o único vetor de SQL injection que restaria neste ponto.
    """
    async with AsyncSessionLocal() as session:
        try:
            async with session.begin():
                # set_config(..., is_local=true) é o equivalente “seguro para bind
                # parameter” de SET LOCAL — permite passar tenant_id como parâmetro
                # real em vez de interpolar a string do comando SQL.
                await session.execute(
                    text("SELECT set_config('app.current_tenant', :tenant_id, true)"),
                    {"tenant_id": tenant_id},
                )
                yield session
            # session.begin() já dá commit automático ao sair do bloco sem exceção,
            # e rollback automático se uma exceção subir — não chamamos commit()
            # manualmente aqui de propósito, para não duplicar o gerenciamento
            # de transação em cada endpoint.
        finally:
            await session.close()
