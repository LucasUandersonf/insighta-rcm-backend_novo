"""
app/scripts/bootstrap_db.py

Script de bootstrap IDEMPOTENTE e AUTOMÁTICO do banco de produção. Roda
uma vez a cada início de processo (via app/scripts/entrypoint.py), e é
seguro rodar em TODO deploy — não só no primeiro.

O QUE ELE FAZ SOZINHO AGORA (sem nenhum passo manual de psql)
-------------------------------------------------------------------------
1. Cria o schema `core` e todas as tabelas (001-004), se ainda não
   existirem.
2. Roda `alembic upgrade head` (aplica a migration 0004 de verdade, e
   as baselines restantes).
3. Aplica os índices de performance (005).
4. **Cria a role `app_runtime`** (não-superusuário, sem BYPASSRLS) e a
   role `auth_resolver_owner` (dona da função de login), com os GRANTs
   corretos — isso ANTES só existia como um script SQL manual
   (006_production_roles.sql) que precisava ser rodado à mão uma vez.
5. Devolve a DSN de conexão como app_runtime, pronta para o entrypoint
   usar ao iniciar a aplicação de verdade.

DECISÃO — duas credenciais diferentes, DATABASE_ADMIN_URL x DATABASE_URL
-------------------------------------------------------------------------
Este script precisa de privilégio de SUPERUSUÁRIO para criar
schema/roles (`DATABASE_ADMIN_URL` — o usuário padrão que o Railway/RDS
já fornece). A APLICAÇÃO, depois de iniciada, nunca deveria usar essa
credencial — só a role restrita `app_runtime`, sujeita a RLS. Por isso
este script COMPUTA a DSN de app_runtime a partir de
DATABASE_ADMIN_URL (host/porta/banco) + APP_RUNTIME_PASSWORD, e é o
entrypoint.py quem decide o que fazer com ela (setar como DATABASE_URL
antes de importar a aplicação).

DECISÃO — por que ler variável de ambiente direto (os.environ), não via
app.core.config.get_settings()
-------------------------------------------------------------------------
`Settings.DATABASE_URL` é um campo OBRIGATÓRIO — mas na primeira
inicialização do processo, antes deste script rodar, `DATABASE_URL`
ainda não existe no ambiente (só `DATABASE_ADMIN_URL` existe, fornecido
pelo usuário). Chamar `get_settings()` neste ponto faria o
pydantic-settings falhar por campo obrigatório ausente — um problema de
ovo-e-galinha. Este script, portanto, não usa a classe Settings para as
variáveis de bootstrap; lê `os.environ` diretamente.

DECISÃO — por que é seguro embutir a senha via f-string no SQL aqui
(diferente do que fizemos em 006_production_roles.sql, que usava o
comando gexec do psql combinado com quote_literal)
-------------------------------------------------------------------------
A senha em uso aqui é gerada pelo próprio operador com
`secrets.token_urlsafe(...)` (documentado no README) — o alfabeto desse
gerador é só [A-Za-z0-9_-], que NUNCA contém aspas. Embutir isso via
f-string não abre brecha de injeção de SQL porque o valor não pode
conter aspas para "escapar" do literal. Isso é validado explicitamente
abaixo (_assert_safe_for_sql_literal) — se algum dia alguém setar uma
senha com aspas manualmente, o script recusa rodar em vez de arriscar.
"""
import asyncio
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
from alembic import command
from alembic.config import Config

logger = logging.getLogger("bootstrap_db")

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_BOOTSTRAP_FILES = [
    "001_init_schema.sql",
    "002_auth_resolver.sql",
    "003_ingestion_tables.sql",
    "004_capacity_management.sql",
]

# BUG CRÍTICO DE PRODUÇÃO CORRIGIDO AQUI (2026-08-29) — leia antes de
# tocar nesta lista de novo
# -------------------------------------------------------------------------
# 005_performance_indexes.sql SEMPRE foi aplicado automaticamente (linha
# "Aplicando índices de performance" em bootstrap(), abaixo). Mas
# 006_platform_admin.sql, 007_contract_intelligence.sql e
# 008_denial_appeals.sql foram documentados nos respectivos
# alembic/versions/000X_*.py como "rode psql -f ... manualmente, depois
# alembic stamp" — e esse passo manual NUNCA foi executado contra o banco
# de produção. O problema: `alembic upgrade head` (chamado abaixo) AVANÇA
# o carimbo de versão através de 0006/0007/0008 mesmo sem a DDL real ter
# rodado, porque upgrade()/downgrade() desses arquivos são no-op de
# propósito (ver DECISÃO nos próprios arquivos) — então o banco fica
# "marcado como atualizado" sem as tabelas/colunas existirem de fato.
# Resultado em produção: TODA rota que toca core.api_keys,
# core.insurance_companies, core.contract_items, billing.received_value/
# settled_at ou core.denial_appeals falha com "column/relation does not
# exist" -> 500 -> a tela mostra "Algo deu errado" (Usuários,
# Integrações, Convênios & Contratos, Sala de Comando, Painel Anti-Glosa,
# Recurso de Glosa — todas essas rotas dependem de pelo menos uma dessas
# três migrations). `core.tenants`/Minha Clínica continuava funcionando
# exatamente porque é a única tela que não toca nenhuma coluna/tabela
# nova.
#
# A correção: aplicar esses três arquivos aqui, no MESMO lugar
# idempotente onde 005 já era aplicado (ver chamada logo após
# `command.upgrade(...)` em bootstrap()) — nunca mais depender de um
# passo manual de deploy que ninguém lembra de repetir a cada sprint.
#
# BUG CRÍTICO DE PRODUÇÃO #2 (2026-08-30) — 002_auth_resolver.sql sofria
# do MESMO problema de fundo que 006/007/008 tinham antes de entrar nesta
# lista: só era aplicado em _BOOTSTRAP_FILES, ou seja, SÓ no primeiríssimo
# bootstrap (schema 'core' ainda não existe). Quando a função
# core.resolve_login mudou de formato (F-04, ver 002_auth_resolver.sql —
# passou a devolver tenant_is_active/tenant_trade_name), todo banco de
# produção JÁ existente nunca recebeu essa atualização, porque o bloco
# "schema já existe, pulando bootstrap SQL bruto (001-004)" pula esse
# arquivo também. Resultado: a função ficou para trás na versão antiga em
# produção, incompatível com auth_repository.py -> todo POST
# /api/v1/auth/login quebrava com `NoSuchColumnError: tenant_trade_name`.
# 002 agora entra aqui (arquivo já reescrito com DROP + CREATE, seguro de
# repetir mesmo trocando o formato de retorno — ver seu próprio cabeçalho)
# e PRECISA rodar antes de `_ensure_roles()` nesta mesma função bootstrap()
# — é `_ROLES_SQL` que faz `ALTER FUNCTION ... OWNER TO auth_resolver_owner`
# e o `GRANT EXECUTE ... TO app_runtime`, e essas duas linhas só fazem
# sentido DEPOIS que a função (re)existe.
_POST_UPGRADE_SQL_FILES = [
    "002_auth_resolver.sql",
    "005_performance_indexes.sql",
    "006_platform_admin.sql",
    "007_contract_intelligence.sql",
    "008_denial_appeals.sql",
    "009_report_recipients.sql",
    "010_ingestion_original_filename.sql",
    "011_annual_revenue_goal.sql",
    # Cadastro público + recuperação de senha (self-signup). Assim como
    # 002_auth_resolver.sql, este arquivo é auto-idempotente por
    # construção (CREATE TABLE IF NOT EXISTS + DROP/CREATE FUNCTION) —
    # roda em TODO deploy, sem entrar em _POST_UPGRADE_MARKER_TABLE.
    "012_password_reset.sql",
    # BUG CRÍTICO DE PRODUÇÃO #3 (achado via scripts/seed_demo_data.py) —
    # mesma categoria de #1/#2 acima: a CHECK constraint de plan_tier
    # ficou para trás em 'pro' enquanto o resto do sistema já usava
    # 'professional' havia tempo. Ver DECISÃO completa no próprio .sql.
    # Auto-idempotente (DROP IF EXISTS + ADD) — roda em todo deploy.
    "013_fix_plan_tier_check.sql",
    # is_active em insurance_companies/insurance_plans (achado do usuário:
    # convênio/plano cadastrado errado não tinha nenhuma forma de sair dos
    # seletores). Auto-idempotente (ADD COLUMN IF NOT EXISTS) — roda em
    # todo deploy. Ver DECISÃO completa no próprio .sql.
    "014_insurance_is_active.sql",
    # Guia (TISS) — Fase 1 do plano de adequação ao fluxo real de mercado
    # (Agendamento -> Atendimento -> Faturamento). CREATE TABLE sem IF NOT
    # EXISTS — precisa do marcador (ver _POST_UPGRADE_MARKER_TABLE), não
    # roda incondicionalmente em todo deploy.
    "015_billing_guia.sql",
    # Lote + Fatura — Fase 2 do plano de adequação ao fluxo real de
    # mercado. CREATE TABLE sem IF NOT EXISTS — precisa do marcador
    # (ver _POST_UPGRADE_MARKER_TABLE).
    "016_lotes_faturas.sql",
    # Glosa REAL — Fase 3 do plano de adequação ao fluxo real de
    # mercado. CREATE TABLE sem IF NOT EXISTS — precisa do marcador
    # (ver _POST_UPGRADE_MARKER_TABLE).
    "017_glosas.sql",
    # Local de Atendimento + Tipo de Paciente — Fase 4 do plano de
    # adequação ao fluxo real de mercado. CREATE TABLE sem IF NOT
    # EXISTS — precisa do marcador (ver _POST_UPGRADE_MARKER_TABLE).
    "018_locais_tipo_paciente.sql",
    # Template de Integração "Agenda" — external_id em appointments +
    # data_type em ingestion_files. Auto-idempotente (ADD COLUMN IF NOT
    # EXISTS + DROP/ADD CONSTRAINT) — roda em todo deploy, sem entrar em
    # _POST_UPGRADE_MARKER_TABLE. Ver DECISÃO completa no próprio .sql.
    "019_agenda_ingestion.sql",
    # Limiares de risco de falta configuráveis por tenant. Auto-idempotente
    # (ADD COLUMN IF NOT EXISTS) — roda em todo deploy, sem entrar em
    # _POST_UPGRADE_MARKER_TABLE. Ver DECISÃO completa no próprio .sql.
    "020_no_show_thresholds.sql",
    # Mapeador automático de coluna — core.ingestion_column_aliases.
    # CREATE TABLE sem IF NOT EXISTS — precisa do marcador (ver
    # _POST_UPGRADE_MARKER_TABLE). Ver DECISÃO completa no próprio .sql.
    "021_ingestion_column_aliases.sql",
]

_ROLES_SQL = """
GRANT USAGE ON SCHEMA core TO app_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core TO app_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA core TO app_runtime;
GRANT EXECUTE ON FUNCTION core.current_tenant_id() TO app_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA core GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA core GRANT USAGE, SELECT ON SEQUENCES TO app_runtime;

GRANT USAGE ON SCHEMA core TO auth_resolver_owner;
GRANT SELECT ON core.users, core.tenants TO auth_resolver_owner;
ALTER FUNCTION core.resolve_login(CITEXT) OWNER TO auth_resolver_owner;
REVOKE ALL ON FUNCTION core.resolve_login(CITEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.resolve_login(CITEXT) TO app_runtime;

-- Cadastro público + recuperação de senha (ver 012_password_reset.sql) —
-- mesmo padrão de resolve_login acima, aplicado só DEPOIS que
-- auth_resolver_owner existe (ver DECISÃO no próprio .sql sobre por que
-- não fica lá).
ALTER FUNCTION core.resolve_user_by_email(CITEXT) OWNER TO auth_resolver_owner;
REVOKE ALL ON FUNCTION core.resolve_user_by_email(CITEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.resolve_user_by_email(CITEXT) TO app_runtime;
"""


class BootstrapError(RuntimeError):
    pass


def _assert_safe_for_sql_literal(value: str, *, var_name: str) -> None:
    # Lista de PERMISSÃO (não de bloqueio): só aceita o alfabeto que
    # secrets.token_urlsafe() produz (A-Z, a-z, 0-9, '-', '_'). Cobre dois
    # riscos de uma vez — aspas quebrando o literal SQL (f-string acima)
    # E caracteres como '@'/':'/'/' quebrando a montagem da URL de conexão
    # em _build_runtime_dsn(). IMPORTANTE: `openssl rand -base64` usa um
    # alfabeto DIFERENTE (inclui '+', '/', '=') que ESTA validação
    # rejeitaria — para APP_RUNTIME_PASSWORD, gere sempre com
    # `python -c "import secrets; print(secrets.token_urlsafe(32))"`,
    # nunca com openssl.
    if not re.fullmatch(r"[A-Za-z0-9._~-]+", value):
        raise BootstrapError(
            f"{var_name} contém caractere fora do alfabeto seguro (letras, números, '.', '_', '~', '-'). "
            "Gere a senha com `python -c \"import secrets; print(secrets.token_urlsafe(32))\"` "
            "para garantir compatibilidade."
        )


def _to_asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _build_runtime_dsn(admin_dsn: str, *, role: str, password: str) -> str:
    """Reaproveita host/porta/nome do banco da DSN admin, trocando só usuário/senha."""
    parts = urlsplit(_to_asyncpg_dsn(admin_dsn))
    netloc = f"{role}:{password}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit(("postgresql+asyncpg", netloc, parts.path, "", ""))


async def _schema_core_exists(dsn: str) -> bool:
    conn = await asyncpg.connect(dsn=dsn)
    try:
        row = await conn.fetchrow(
            "SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = 'core')"
        )
        return bool(row[0])
    finally:
        await conn.close()


async def _table_exists(dsn: str, table_name: str) -> bool:
    conn = await asyncpg.connect(dsn=dsn)
    try:
        row = await conn.fetchrow(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'core' AND table_name = $1)",
            table_name,
        )
        return bool(row[0])
    finally:
        await conn.close()


async def _run_sql_files(dsn: str, filenames: list[str]) -> None:
    conn = await asyncpg.connect(dsn=dsn)
    try:
        for filename in filenames:
            logger.info("Aplicando %s...", filename)
            await conn.execute((_SQL_DIR / filename).read_text())
    finally:
        await conn.close()


# CADA arquivo em _POST_UPGRADE_SQL_FILES roda em TODO deploy (ver
# DECISÃO acima) — mas nenhum deles é idempotente por si só (CREATE
# TABLE/INDEX/POLICY sem IF NOT EXISTS, herdado de quando eram pensados
# como "rode uma vez, manualmente"). Em vez de reescrever cada .sql já
# revisado para ser idempotente (superfície maior de mudança em DDL
# sensível a RLS), este mapa dá a cada arquivo um "objeto-marcador":
# uma tabela que só existe DEPOIS dele ter rodado — mesmo raciocínio de
# `_schema_core_exists` para o bootstrap 001-004, replicado por arquivo.
# 005 (índices) fica de fora do mapa porque `CREATE INDEX IF NOT EXISTS`
# já é idempotente nele — sempre pôde rodar em todo deploy sem marcador.
_POST_UPGRADE_MARKER_TABLE = {
    "006_platform_admin.sql": "api_keys",
    "007_contract_intelligence.sql": "insurance_companies",
    "008_denial_appeals.sql": "denial_appeals",
    "009_report_recipients.sql": "report_recipients",
    "015_billing_guia.sql": "guias",
    "016_lotes_faturas.sql": "lotes",
    "017_glosas.sql": "glosas",
    "018_locais_tipo_paciente.sql": "locais",
    "021_ingestion_column_aliases.sql": "ingestion_column_aliases",
}


async def _run_post_upgrade_sql_files_idempotent(dsn: str, filenames: list[str]) -> None:
    conn = await asyncpg.connect(dsn=dsn)
    try:
        for filename in filenames:
            marker = _POST_UPGRADE_MARKER_TABLE.get(filename)
            if marker is not None:
                row = await conn.fetchrow(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'core' AND table_name = $1)",
                    marker,
                )
                if bool(row[0]):
                    logger.info("%s já aplicado (tabela core.%s existe) — pulando.", filename, marker)
                    continue
            logger.info("Aplicando %s...", filename)
            await conn.execute((_SQL_DIR / filename).read_text())
    finally:
        await conn.close()


async def _ensure_roles(admin_dsn: str, *, app_runtime_password: str) -> None:
    conn = await asyncpg.connect(dsn=admin_dsn)
    try:
        app_runtime_exists = await conn.fetchval("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_runtime')")
        if not app_runtime_exists:
            logger.info("Criando role app_runtime...")
            # Ver DECISÃO no topo do arquivo sobre por que isto é seguro
            # apesar de parecer uma f-string "perigosa": a senha já foi
            # validada por _assert_safe_for_sql_literal antes de chegar aqui.
            await conn.execute(
                f"CREATE ROLE app_runtime LOGIN PASSWORD '{app_runtime_password}' NOSUPERUSER NOBYPASSRLS"
            )
        else:
            logger.info("Role app_runtime já existe — atualizando senha para o valor atual de APP_RUNTIME_PASSWORD.")
            await conn.execute(f"ALTER ROLE app_runtime WITH PASSWORD '{app_runtime_password}'")

        auth_owner_exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'auth_resolver_owner')"
        )
        if not auth_owner_exists:
            logger.info("Criando role auth_resolver_owner...")
            # BYPASSRLS aqui só ignora POLÍTICAS de RLS — os GRANTs de
            # schema/tabela abaixo (_ROLES_SQL) continuam necessários,
            # mesmo com BYPASSRLS setado (bug real que já apareceu antes
            # nesta jornada: ver comentário em _ROLES_SQL).
            await conn.execute("CREATE ROLE auth_resolver_owner NOLOGIN NOSUPERUSER BYPASSRLS")

        logger.info("Aplicando GRANTs (app_runtime, auth_resolver_owner)...")
        await conn.execute(_ROLES_SQL)
    finally:
        await conn.close()


def _alembic_config() -> Config:
    cfg = Config(str(_PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "alembic"))
    return cfg


def bootstrap() -> str:
    """Retorna a DATABASE_URL (app_runtime) já pronta para a aplicação usar."""
    admin_dsn = os.environ.get("DATABASE_ADMIN_URL")
    app_runtime_password = os.environ.get("APP_RUNTIME_PASSWORD")

    if not admin_dsn:
        raise BootstrapError(
            "DATABASE_ADMIN_URL não configurada. Aponte para a conexão de "
            "superusuário do Postgres (ex: a DATABASE_URL padrão que o "
            "Railway/RDS já fornece) — usada só neste bootstrap, nunca pela "
            "aplicação em si."
        )
    if not app_runtime_password:
        raise BootstrapError(
            "APP_RUNTIME_PASSWORD não configurada. Gere uma com "
            "`python -c \"import secrets; print(secrets.token_urlsafe(32))\"` "
            "e configure essa variável de ambiente uma única vez."
        )
    _assert_safe_for_sql_literal(app_runtime_password, var_name="APP_RUNTIME_PASSWORD")

    admin_dsn_asyncpg = _to_asyncpg_dsn(admin_dsn)

    # Alembic (via alembic/env.py) lê DATABASE_URL através de
    # get_settings() — tanto `stamp` quanto `upgrade` precisam dela, e
    # DDL (ADD COLUMN, CREATE INDEX) exige privilégio que app_runtime não
    # tem, nem deveria ter. Por isso setamos para a credencial de
    # SUPERUSUÁRIO logo no início, ANTES de qualquer comando do Alembic
    # — não no meio, onde `stamp()` já teria rodado sem essa variável
    # sequer existir no ambiente.
    admin_dsn_sqlalchemy = (
        admin_dsn if admin_dsn.startswith("postgresql+asyncpg://") else admin_dsn.replace("postgresql://", "postgresql+asyncpg://")
    )
    os.environ["DATABASE_URL"] = admin_dsn_sqlalchemy

    if asyncio.run(_schema_core_exists(admin_dsn_asyncpg)):
        logger.info("Schema 'core' já existe — pulando bootstrap SQL bruto (001-004).")
    else:
        logger.info("Schema 'core' não existe — rodando bootstrap completo pela primeira vez.")
        asyncio.run(_run_sql_files(admin_dsn_asyncpg, _BOOTSTRAP_FILES))
        command.stamp(_alembic_config(), "0003_capacity_baseline")

    logger.info("Rodando alembic upgrade head (como superusuário)...")
    command.upgrade(_alembic_config(), "head")

    # ORDEM IMPORTA: isto precisa rodar ANTES de _ensure_roles() logo
    # abaixo. 002_auth_resolver.sql está nesta lista (ver DECISÃO em
    # _POST_UPGRADE_SQL_FILES acima) e recria core.resolve_login do zero
    # (DROP + CREATE) — _ensure_roles() é quem, na sequência, aplica
    # `ALTER FUNCTION ... OWNER TO auth_resolver_owner` e
    # `GRANT EXECUTE ... TO app_runtime` sobre essa função (_ROLES_SQL).
    # Se a ordem fosse invertida, esses dois comandos rodariam contra a
    # função ANTIGA (ou, pior, contra nenhuma função na primeiríssima vez)
    # e a função recém-recriada ficaria sem GRANT EXECUTE para app_runtime
    # — login quebraria de novo, com "permission denied" desta vez.
    logger.info("Aplicando arquivos SQL pós-upgrade (002, 005-011, idempotente)...")
    asyncio.run(_run_post_upgrade_sql_files_idempotent(admin_dsn_asyncpg, _POST_UPGRADE_SQL_FILES))

    logger.info("Garantindo roles de produção (app_runtime, auth_resolver_owner)...")
    asyncio.run(_ensure_roles(admin_dsn_asyncpg, app_runtime_password=app_runtime_password))

    runtime_dsn = _build_runtime_dsn(admin_dsn, role="app_runtime", password=app_runtime_password)
    logger.info("Bootstrap concluído com sucesso.")
    return runtime_dsn


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    bootstrap()
