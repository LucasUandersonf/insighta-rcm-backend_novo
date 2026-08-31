"""
app/scripts/entrypoint.py

Ponto de entrada ÚNICO em produção — substitui a cadeia anterior
"bootstrap_db.py && uvicorn ..." (dois processos separados) por UM
processo só. Isso importa: a DATABASE_URL que bootstrap_db.py calcula
(role app_runtime) só chega até a aplicação se for setada ANTES do
primeiro import de app.main — e "antes" só existe garantido dentro do
MESMO processo Python. Dois comandos encadeados por "&&" no shell são
dois processos distintos; uma variável setada via os.environ no
primeiro nunca chega ao segundo.

Uso (railway.toml):
    startCommand = "python -m app.scripts.entrypoint"

Variáveis de ambiente necessárias (só estas duas, configuradas UMA VEZ):
    DATABASE_ADMIN_URL   — a DATABASE_URL de superusuário que o
                           Railway/RDS já fornece (ex: aponte para a
                           variável do próprio serviço de Postgres).
    APP_RUNTIME_PASSWORD — qualquer senha forte, gerada uma vez com
                           `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
    JWT_SECRET_KEY       — já era necessária antes; continua sendo.

DATABASE_URL não precisa mais ser setada manualmente — este script a
calcula sozinho a partir das duas primeiras.
"""
import logging
import os
import sys

from app.scripts.bootstrap_db import BootstrapError, bootstrap

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("entrypoint")


def main() -> None:
    try:
        runtime_dsn = bootstrap()
    except BootstrapError as exc:
        # Falha ALTA e CLARA — evita a aplicação subir "meio funcionando"
        # com um banco em estado incerto. O deploy falha visivelmente no
        # log, com a instrução exata do que falta configurar.
        logger.error("Bootstrap falhou: %s", exc)
        sys.exit(1)

    # A partir daqui, TODO import de app.* que precise de configuração de
    # banco (app.core.config.get_settings(), app.db.session, etc.) já vai
    # ler a DATABASE_URL correta — porque ela é setada ANTES desses
    # módulos serem importados pela primeira vez neste processo.
    os.environ["DATABASE_URL"] = runtime_dsn

    # IMPORTANTE: get_settings() já foi chamado UMA VEZ dentro de
    # bootstrap() (indiretamente, via alembic/env.py, durante o
    # `alembic upgrade head`) — com a DATABASE_URL do SUPERUSUÁRIO
    # setada temporariamente. Como get_settings() é cacheado
    # (@lru_cache), só trocar a variável de ambiente NÃO seria
    # suficiente: a instância antiga (com a credencial errada) ainda
    # seria devolvida em toda chamada seguinte. cache_clear() força uma
    # leitura nova do ambiente na próxima vez que qualquer módulo
    # chamar get_settings() — o que acontece já a seguir, ao importar
    # app.main.
    from app.core.config import get_settings

    get_settings.cache_clear()

    import uvicorn

    from app.main import app  # import tardio de propósito — ver docstring

    port = int(os.environ.get("PORT", "8000"))
    logger.info("Iniciando uvicorn na porta %d...", port)
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
