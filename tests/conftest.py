"""
tests/conftest.py

Infraestrutura de testes de INTEGRAÇÃO — requer um Postgres real (ver
README, seção "Rodando os testes de integração"). Os testes de unidade
já existentes (test_denial_risk_engine.py, test_no_show_risk_engine.py,
etc.) não passam por este arquivo — são funções puras, sem banco.

DECISÃO CRÍTICA #1 — Ordem de import: variáveis de ambiente ANTES de
qualquer `from app... import`
-------------------------------------------------------------------------
app/db/session.py cria o engine do SQLAlchemy em nível de MÓDULO
(`engine = create_async_engine(settings.DATABASE_URL, ...)`), não dentro
de uma função. Isso significa que a URL do banco é fixada no momento em
que esse módulo é importado PELA PRIMEIRA VEZ no processo — depois disso,
o Python reaproveita o módulo já carregado (sys.modules), e trocar
variável de ambiente não tem mais efeito nenhum. Por isso as variáveis
de ambiente de teste são setadas nas PRIMEIRAS linhas deste arquivo,
antes de qualquer import de `app.*` — inclusive antes dos imports do
pytest/httpx que vêm depois, para deixar a ordem inequívoca.

DECISÃO CRÍTICA #2 — Por que os testes NÃO podem conectar como
superusuário do Postgres
-------------------------------------------------------------------------
Um role com atributo SUPERUSER (ou BYPASSRLS) IGNORA Row-Level Security
por definição do próprio Postgres — nem `FORCE ROW LEVEL SECURITY` muda
isso. Se os testes conectassem como o usuário padrão `postgres` (comum
em qualquer instalação local), os testes de isolamento entre tenants
"passariam" mesmo com o RLS quebrado — falsa confiança exatamente na
garantia de segurança mais crítica do projeto. Por isso este arquivo:
  1) conecta como superusuário SÓ para criar banco/roles/schema (DDL
     administrativo não pode rodar de outra forma);
  2) cria de verdade as roles `app_test_runtime` (NOSUPERUSER,
     NOBYPASSRLS) e a dona da função de login (BYPASSRLS, mas só ela) —
     que até agora só existiam COMENTADAS em 001_init_schema.sql e
     002_auth_resolver.sql. Esta é a primeira execução real dessas
     linhas em todo o projeto;
  3) faz a APLICAÇÃO (o engine que o FastAPI usa) conectar como
     `app_test_runtime`, não como o superusuário — só assim um teste que
     tenta ler dado de outro tenant está de fato sujeito ao RLS.

DECISÃO #3 — banco de teste descartável, criado e destruído por sessão
-------------------------------------------------------------------------
Cada execução da suíte cria um banco com nome único
(`test_rcm_<hex aleatório>`), aplica o schema do zero, e derruba tudo no
final — nunca reaproveita nem risca de colidir com um banco de
desenvolvimento que porventura já exista.
"""
import os
import uuid as uuid_module

# ---------------------------------------------------------------------
# PASSO 1 — variáveis de ambiente de teste, ANTES de qualquer import de
# app.*. Ver DECISÃO CRÍTICA #1 acima.
# ---------------------------------------------------------------------
_TEST_DB_NAME = f"test_rcm_{uuid_module.uuid4().hex[:8]}"
_ADMIN_DSN = os.environ.get("TEST_DATABASE_ADMIN_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
_APP_ROLE_PASSWORD = "app_test_runtime_pw"

_admin_base = _ADMIN_DSN.rsplit("/", 1)[0]  # remove só o nome do banco final ("/postgres") — mantém usuário/senha do admin, usado para conectar como superusuário

# Extrai APENAS host:porta da DSN admin (removendo esquema, credenciais
# do admin e nome do banco) para montar a DSN da role de teste sem
# herdar as credenciais do superusuário coladas na string — bug real que
# já apareceu aqui antes: "usuario_teste:senha@postgres:postgres@host:porta"
# (duas credenciais concatenadas) quando essa extração ficava incompleta.
_admin_no_scheme = _ADMIN_DSN.split("://", 1)[1]              # "postgres:postgres@localhost:5432/postgres"
_admin_host_port = _admin_no_scheme.split("@", 1)[-1].rsplit("/", 1)[0]  # "localhost:5432"

os.environ["DATABASE_URL"] = f"postgresql+asyncpg://app_test_runtime:{_APP_ROLE_PASSWORD}@{_admin_host_port}/{_TEST_DB_NAME}"
os.environ["JWT_SECRET_KEY"] = "chave-de-teste-nao-usar-em-producao"
os.environ["ENVIRONMENT"] = "test"
os.environ.setdefault("RATE_LIMIT_DEFAULT", "1000/minute")  # evita 429 nos testes por causa do rate limit geral
os.environ.setdefault("LOGIN_RATE_LIMIT", "10000/minute")  # login é chamado dezenas de vezes pelos testes, todos "do mesmo IP" simulado

from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

_SQL_DIR = Path(__file__).resolve().parent.parent / "app" / "sql"
_PWD_CONTEXT = CryptContext(schemes=["argon2"], deprecated="auto")

# Mesma lista, na mesma ordem, dos scripts que o README manda rodar
# manualmente em produção — reaproveitar essa lista aqui garante que os
# testes validem exatamente o procedimento documentado, não uma versão
# paralela dele.
_SCHEMA_FILES = [
    "001_init_schema.sql",
    "002_auth_resolver.sql",
    "003_ingestion_tables.sql",
    "004_capacity_management.sql",
    "005_performance_indexes.sql",
    "006_platform_admin.sql",
    "007_contract_intelligence.sql",
    "008_denial_appeals.sql",
    "009_report_recipients.sql",
    "010_ingestion_original_filename.sql",
    "011_annual_revenue_goal.sql",
    "012_password_reset.sql",
    "013_fix_plan_tier_check.sql",
    "014_insurance_is_active.sql",
    "015_billing_guia.sql",
    "016_lotes_faturas.sql",
    "017_glosas.sql",
    "018_locais_tipo_paciente.sql",
]

# DDL da migration 0004 (adicionada via Alembic normal, não um arquivo em
# app/sql/ — ver alembic/versions/0004_add_no_show_risk_fields.py).
# Reproduzida aqui em vez de invocar o Alembic via subprocess para manter
# o bootstrap de teste rápido e síncrono com o resto deste arquivo; se
# o schema divergir da migration real, os testes de risco de falta
# (test_no_show_risk.py) quebram e isso fica visível.
_NO_SHOW_RISK_DDL = """
ALTER TABLE core.appointments
    ADD COLUMN IF NOT EXISTS no_show_risk_level VARCHAR(20),
    ADD COLUMN IF NOT EXISTS no_show_risk_score NUMERIC(5,4);
"""

# Roles que até agora só existiam COMENTADAS no SQL de produção (ver
# DECISÃO CRÍTICA #2). Executadas de verdade aqui pela primeira vez.
_ROLES_BOOTSTRAP_SQL = f"""
DROP ROLE IF EXISTS app_test_runtime;
CREATE ROLE app_test_runtime LOGIN PASSWORD '{_APP_ROLE_PASSWORD}' NOSUPERUSER NOBYPASSRLS;
GRANT USAGE ON SCHEMA core TO app_test_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core TO app_test_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA core TO app_test_runtime;
GRANT EXECUTE ON FUNCTION core.current_tenant_id() TO app_test_runtime;

DROP ROLE IF EXISTS auth_resolver_owner_test;
CREATE ROLE auth_resolver_owner_test NOLOGIN NOSUPERUSER;
ALTER ROLE auth_resolver_owner_test BYPASSRLS;
-- IMPORTANTE: BYPASSRLS só ignora políticas de RLS — NÃO ignora GRANTs
-- básicos de schema/tabela. A função core.resolve_login roda com os
-- privilégios DESTA role (SECURITY DEFINER), então ela precisa, além do
-- BYPASSRLS, de USAGE no schema e SELECT nas tabelas que a função lê
-- internamente (core.users, core.tenants) — sem isso, "permission denied
-- for schema core" mesmo com BYPASSRLS setado.
GRANT USAGE ON SCHEMA core TO auth_resolver_owner_test;
GRANT SELECT ON core.users, core.tenants TO auth_resolver_owner_test;
ALTER FUNCTION core.resolve_login(CITEXT) OWNER TO auth_resolver_owner_test;
REVOKE ALL ON FUNCTION core.resolve_login(CITEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.resolve_login(CITEXT) TO app_test_runtime;

-- Mesmo padrão acima, para o resolver de recuperação de senha (ver
-- 012_password_reset.sql e _ROLES_SQL em app/scripts/bootstrap_db.py).
ALTER FUNCTION core.resolve_user_by_email(CITEXT) OWNER TO auth_resolver_owner_test;
REVOKE ALL ON FUNCTION core.resolve_user_by_email(CITEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.resolve_user_by_email(CITEXT) TO app_test_runtime;
"""


def _requires_test_database():
    if "TEST_DATABASE_ADMIN_URL" not in os.environ and not os.environ.get("TEST_DATABASE_ALLOW_DEFAULT"):
        pytest.skip(
            "TEST_DATABASE_ADMIN_URL não configurada — testes de integração pulados. "
            "Ver README, seção 'Rodando os testes de integração'."
        )


@pytest.fixture(scope="session")
def app_runtime_dsn() -> str:
    """
    A MESMA DSN que a aplicação usa de verdade (app_test_runtime),
    montada uma única vez no topo deste arquivo. Reaproveitada em testes
    que precisam abrir uma segunda conexão como essa role — evita
    reconstruir a DSN via manipulação de string a partir de outra DSN
    (frágil: quebraria silenciosamente se as credenciais do superusuário
    fossem diferentes de "postgres:postgres").
    """
    return os.environ["DATABASE_URL"]


@pytest_asyncio.fixture(scope="session")
async def _test_database() -> AsyncGenerator[str, None]:
    """
    Cria o banco de teste + roles + schema completo UMA VEZ para toda a
    sessão de testes, e derruba tudo ao final. asyncpg puro (não
    SQLAlchemy) porque CREATE DATABASE não pode rodar dentro de uma
    transação, e os scripts em app/sql/ usam blocos DO $$ ... $$ que o
    asyncpg executa nativamente como múltiplos statements.
    """
    _requires_test_database()

    admin_conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    try:
        await admin_conn.execute(f'CREATE DATABASE "{_TEST_DB_NAME}"')
    finally:
        await admin_conn.close()

    db_dsn = f"{_admin_base}/{_TEST_DB_NAME}"
    db_conn = await asyncpg.connect(dsn=db_dsn)
    try:
        for filename in _SCHEMA_FILES:
            sql_text = (_SQL_DIR / filename).read_text()
            await db_conn.execute(sql_text)
        await db_conn.execute(_NO_SHOW_RISK_DDL)
        await db_conn.execute(_ROLES_BOOTSTRAP_SQL)
    finally:
        await db_conn.close()

    yield db_dsn

    # Derruba o banco de teste. Precisa encerrar conexões residentes
    # primeiro (o pool do SQLAlchemy pode ainda ter conexões abertas),
    # senão DROP DATABASE falha com "database is being accessed by other users".
    admin_conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    try:
        await admin_conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
            _TEST_DB_NAME,
        )
        await admin_conn.execute(f'DROP DATABASE IF EXISTS "{_TEST_DB_NAME}"')
    finally:
        await admin_conn.close()


@pytest_asyncio.fixture(scope="session")
async def app_instance(_test_database):
    """
    Importa app.main SÓ AGORA — depois que DATABASE_URL já aponta para o
    banco de teste (setado no topo do arquivo) e depois que o schema já
    existe. Import tardio de propósito: se algo importasse `app.main` no
    topo deste arquivo, o engine seria criado apontando para o banco
    errado (ou para um banco que ainda nem existe).
    """
    from app.main import app as fastapi_app

    return fastapi_app


@pytest_asyncio.fixture
async def client(app_instance) -> AsyncGenerator[AsyncClient, None]:
    """Cliente HTTP que conversa com o app em processo (ASGI), sem precisar de uvicorn rodando."""
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def admin_engine(_test_database) -> AsyncGenerator[AsyncEngine, None]:
    """
    Engine conectado como SUPERUSUÁRIO, mas ao BANCO DE TESTE (não ao
    banco 'postgres' de manutenção usado só para CREATE/DROP DATABASE).
    `_test_database` já retorna a DSN correta, apontando para
    test_rcm_<hex>. Usado só para popular dado de setup direto no banco,
    sem passar pela API. Nunca usado para as chamadas que devem estar
    sujeitas a RLS.
    """
    db_dsn = _test_database
    engine = create_async_engine(db_dsn.replace("postgresql://", "postgresql+asyncpg://"))
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_tables(_test_database, admin_engine):
    """
    Roda ANTES de cada teste: esvazia todas as tabelas tenant-scoped.
    autouse=True para nenhum teste esquecer de limpar o estado do
    anterior. RESTART IDENTITY zera sequences (ex: audit_log.id,
    ingestion_raw_rows.id) para os testes não dependerem de IDs
    "adivinhados" de execuções anteriores.

    Ordem implícita importante: fixtures autouse são instanciadas ANTES
    de fixtures não-autouse do mesmo escopo (regra padrão do pytest) —
    é isso que garante que `tenant_a`/`owner_a` (que não dependem
    diretamente desta fixture) sempre insiram DEPOIS da limpeza, nunca
    antes. Se essa premissa mudar, os testes começam a ver dado de uma
    execução anterior.
    """
    async with admin_engine.begin() as conn:
        from sqlalchemy import text

        await conn.execute(
            text(
                """
                TRUNCATE TABLE
                    core.audit_log, core.marketing_webhook_events, core.ingestion_raw_rows,
                    core.ingestion_files, core.marketing_spend,
                    core.report_recipients,
                    core.denial_appeal_attachments, core.denial_appeals, core.billing, core.appointments,
                    core.contract_items, core.contracts, core.insurance_plan_aliases, core.insurance_plans,
                    core.insurance_companies, core.patients,
                    core.professional_availability, core.professionals, core.api_keys, core.users, core.tenants
                RESTART IDENTITY CASCADE
                """
            )
        )
    yield


async def _insert_tenant(admin_engine: AsyncEngine, *, trade_name: str) -> str:
    from sqlalchemy import text

    tenant_id = str(uuid_module.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO core.tenants (id, legal_name, trade_name, cnpj, is_active)
                VALUES (:id, :legal_name, :trade_name, :cnpj, true)
                """
            ),
            {"id": tenant_id, "legal_name": trade_name, "trade_name": trade_name, "cnpj": f"{uuid_module.uuid4().hex[:14]}"},
        )
    return tenant_id


async def _insert_user(admin_engine: AsyncEngine, *, tenant_id: str, email: str, role: str, password: str = "senha-teste-123") -> dict:
    from sqlalchemy import text

    user_id = str(uuid_module.uuid4())
    hashed = _PWD_CONTEXT.hash(password)
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO core.users (id, tenant_id, email, hashed_password, full_name, role, is_active)
                VALUES (:id, :tenant_id, :email, :hashed, :full_name, :role, true)
                """
            ),
            {"id": user_id, "tenant_id": tenant_id, "email": email, "hashed": hashed, "full_name": email.split("@")[0], "role": role},
        )
    return {"id": user_id, "tenant_id": tenant_id, "email": email, "role": role, "password": password}


@pytest_asyncio.fixture
async def tenant_a(admin_engine) -> str:
    return await _insert_tenant(admin_engine, trade_name="Clínica A")


@pytest_asyncio.fixture
async def tenant_b(admin_engine) -> str:
    return await _insert_tenant(admin_engine, trade_name="Clínica B")


@pytest_asyncio.fixture
async def owner_a(admin_engine, tenant_a) -> dict:
    return await _insert_user(admin_engine, tenant_id=tenant_a, email="owner.a@clinica-a.com", role="owner")


@pytest_asyncio.fixture
async def owner_b(admin_engine, tenant_b) -> dict:
    return await _insert_user(admin_engine, tenant_id=tenant_b, email="owner.b@clinica-b.com", role="owner")


async def _login(client: AsyncClient, email: str, password: str) -> str:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, f"login falhou: {response.status_code} {response.text}"
    return response.json()["access_token"]


@pytest_asyncio.fixture
async def token_a(client, owner_a) -> str:
    return await _login(client, owner_a["email"], owner_a["password"])


@pytest_asyncio.fixture
async def token_b(client, owner_b) -> str:
    return await _login(client, owner_b["email"], owner_b["password"])


@pytest.fixture
def auth_headers_a(token_a) -> dict:
    return {"Authorization": f"Bearer {token_a}"}


@pytest.fixture
def auth_headers_b(token_b) -> dict:
    return {"Authorization": f"Bearer {token_b}"}
