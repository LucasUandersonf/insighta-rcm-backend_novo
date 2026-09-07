"""
app/scripts/verify_bootstrap_from_zero.py

Roda de verdade o MESMO bootstrap que sobe em produção
(`app.scripts.bootstrap_db.bootstrap()`) contra um Postgres que não tem
absolutamente nada — nem o schema `core`, nem nenhuma role — e confirma
que a aplicação consegue de fato se conectar e ler dado no final.

Achado do Laudo de Vistoria Técnica / item 3 do "Caminho para produção":
"o cenário de banco vazio nunca foi testado" — os testes de integração
(tests/conftest.py) usam de propósito um atalho síncrono (SQL bruto +
uma migration reproduzida à mão) para ficarem rápidos, então NUNCA
exercitam o `alembic upgrade head` real nem a criação das roles de
produção (`_ensure_roles`). Este script fecha esse ponto cego: roda o
procedimento EXATO que `entrypoint.py` roda no primeiro deploy de um
ambiente novo, contra um banco genuinamente do zero.

Uso (local ou CI, nunca contra um banco com dado real — DROP/CREATE
schema à vontade):
    export DATABASE_ADMIN_URL="postgresql://postgres:postgres@localhost:5432/postgres"
    export APP_RUNTIME_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
    export JWT_SECRET_KEY="qualquer-valor-para-este-teste"
    python -m app.scripts.verify_bootstrap_from_zero
"""
import asyncio
import logging
import sys

import asyncpg

from app.scripts.bootstrap_db import BootstrapError, _to_asyncpg_dsn, bootstrap

logger = logging.getLogger("verify_bootstrap_from_zero")

# Contagem mínima, não exata: só precisa provar que o schema inteiro
# (as 31 migrations) realmente aplicou, sem acoplar este script a um
# número que teria que ser atualizado a cada nova tabela.
_MIN_EXPECTED_TABLES = 30


async def _check_runtime_connection(runtime_dsn: str) -> None:
    """Conecta EXATAMENTE como a aplicação conecta em produção (role
    app_runtime, não superusuário) — a mesma DSN que entrypoint.py usa
    para iniciar o uvicorn. Provar que o bootstrap "rodou sem erro" não
    basta; o que importa é a aplicação de verdade conseguir logar e ler
    dado depois."""
    conn = await asyncpg.connect(dsn=_to_asyncpg_dsn(runtime_dsn))
    try:
        current_user = await conn.fetchval("SELECT current_user")
        if current_user != "app_runtime":
            raise SystemExit(f"Conectou como '{current_user}', esperava 'app_runtime' — DSN de runtime incorreta.")

        table_count = await conn.fetchval(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'core'"
        )
        if table_count < _MIN_EXPECTED_TABLES:
            raise SystemExit(
                f"Só {table_count} tabelas em 'core' (esperava pelo menos {_MIN_EXPECTED_TABLES}) — "
                "o schema não subiu por completo."
            )

        # Confirma que a role app_runtime realmente tem os GRANTs
        # aplicados por _ensure_roles(), não só que a role existe —
        # SELECT numa tabela protegida por RLS é o teste real: sem
        # GRANT de schema/tabela, isto falha com "permission denied"
        # mesmo antes de qualquer política de RLS entrar em jogo.
        await conn.fetchval("SELECT count(*) FROM core.tenants")

        logger.info(
            "OK: conectado como app_runtime, %d tabelas em 'core', SELECT em core.tenants permitido.",
            table_count,
        )
    finally:
        await conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        runtime_dsn = bootstrap()
    except BootstrapError as exc:
        logger.error("Bootstrap falhou contra banco vazio: %s", exc)
        sys.exit(1)

    try:
        asyncio.run(_check_runtime_connection(runtime_dsn))
    except SystemExit as exc:
        logger.error("Verificação pós-bootstrap falhou: %s", exc)
        sys.exit(1)

    logger.info("Bootstrap do zero verificado com sucesso — banco novo, sem nenhum passo manual, pronto para a aplicação.")


if __name__ == "__main__":
    main()
