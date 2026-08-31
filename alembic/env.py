"""
alembic/env.py

DECISÃO ARQUITETURAL — Alembic assíncrono, apontando para os mesmos
Settings da aplicação
-------------------------------------------------------------------------
Como o projeto usa SQLAlchemy assíncrono (asyncpg), o Alembic também
precisa rodar as migrations através de um engine assíncrono. O padrão
oficial do Alembic para isso é: abrir uma AsyncEngine, pegar uma
conexão, e usar `connection.run_sync(...)` para executar a parte
síncrona do Alembic (que internamente não conhece async) dentro dessa
conexão async.

Não hardcodamos a URL do banco aqui nem em alembic.ini — lemos de
`get_settings().DATABASE_URL`, a MESMA fonte de verdade usada por
app/db/session.py. Isso elimina uma classe inteira de bugs "migração
rodou no banco errado porque o .env do alembic estava desatualizado".

IMPORTANTE — RLS durante migrations
-------------------------------------------------------------------------
As migrations DEVEM rodar com uma role que tenha permissão de DDL
(tipicamter uma role de "migrator", distinta da role de runtime da
aplicação — ver DECISÃO #8 em 001_init_schema.sql). Como estamos fazendo
ALTER TABLE / CREATE TABLE, e não SELECT/INSERT/UPDATE/DELETE em dados de
tenant, o RLS (que filtra LINHAS) simplesmente não entra em jogo aqui —
FORCE ROW LEVEL SECURITY não impede DDL, só filtra o acesso a dados.
"""
import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Importa TODOS os models (via app/models/__init__.py) para que
# Base.metadata fique completo antes do autogenerate comparar o estado
# do banco com o estado esperado pelo código.
from app.core.config import get_settings
from app.db.base import Base
import app.models  # noqa: F401  (import necessário só pelo efeito colateral de registrar os models)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Injeta a URL vinda do .env da aplicação na config do Alembic em
# tempo de execução, em vez de deixar alembic.ini com a URL fixa.
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Gera SQL sem conectar no banco (`alembic upgrade --sql`)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # version_table_schema garante que a tabela de controle do Alembic
        # (alembic_version) fique dentro do schema "core", junto do resto,
        # em vez de cair em "public" por padrão.
        version_table_schema="core",
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema="core",
        include_schemas=True,
        # compare_type/compare_server_default habilitados: sem isso o
        # autogenerate só detecta colunas adicionadas/removidas, não
        # mudanças de tipo (ex: VARCHAR(20) -> VARCHAR(30)).
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # migration é uma execução pontual, não precisa de pool
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
