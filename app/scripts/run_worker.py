"""
app/scripts/run_worker.py

Ponto de entrada para jobs agendados que NÃO SÃO o servidor web (ex:
app/worker/health_score_snapshot_job.py) — mesmo problema documentado
em app/scripts/entrypoint.py: a DATABASE_URL de app_runtime só existe
DEPOIS de bootstrap() rodar, e precisa ser setada ANTES do primeiro
import de qualquer módulo que leia app.core.config/app.db.session,
dentro do MESMO processo Python (dois comandos encadeados por "&&" no
shell não bastam — são processos distintos).

Uso (startCommand de um serviço de cron no Railway):
    python -m app.scripts.run_worker app.worker.health_score_snapshot_job

Mesmas 2 variáveis de app/scripts/entrypoint.py (DATABASE_ADMIN_URL,
APP_RUNTIME_PASSWORD) — reaproveita a MESMA role app_runtime já criada
pelo serviço web (bootstrap() é idempotente: com a role já existindo,
só devolve a DSN e confere que o schema está em dia, não recria nada).

DECISÃO — genérico (recebe o módulo por argumento), não um script por job
-------------------------------------------------------------------------
Todo worker de execução única deste projeto (weekly_report_job.py,
platform_risk_alert_job.py, webhook_retry_job.py, e agora
health_score_snapshot_job.py) tem exatamente o mesmo problema de
bootstrap — escrever um wrapper por job duplicaria a mesma lógica 4
vezes. Este arquivo resolve para todos de uma vez: qualquer worker cujo
módulo exponha uma função `async def run()` (todos já expõem, ver
`if __name__ == "__main__": asyncio.run(run())` em cada um) pode ser
agendado assim, sem precisar de código novo.
"""
import asyncio
import importlib
import logging
import os
import sys

from app.scripts.bootstrap_db import BootstrapError, bootstrap

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("run_worker")


def main() -> None:
    if len(sys.argv) != 2:
        logger.error("Uso: python -m app.scripts.run_worker <módulo.do.worker> (ex: app.worker.health_score_snapshot_job)")
        sys.exit(1)
    module_path = sys.argv[1]

    try:
        runtime_dsn = bootstrap()
    except BootstrapError as exc:
        # Mesma falha ALTA e CLARA de entrypoint.py — evita o job rodar
        # contra um banco em estado incerto.
        logger.error("Bootstrap falhou: %s", exc)
        sys.exit(1)

    # A partir daqui, todo import de app.* que precise de configuração
    # de banco já vai ler a DATABASE_URL correta — ver DECISÃO completa
    # em entrypoint.py sobre por que isso precisa ser feito ANTES do
    # import tardio abaixo, dentro do MESMO processo.
    os.environ["DATABASE_URL"] = runtime_dsn

    from app.core.config import get_settings

    get_settings.cache_clear()

    # Import tardio de propósito (mesmo raciocínio de entrypoint.py) —
    # o módulo do worker importa app.db.session/app.core.config nos seus
    # próprios imports de topo, que só devem acontecer DEPOIS da
    # DATABASE_URL já estar correta no ambiente.
    worker_module = importlib.import_module(module_path)
    logger.info("Executando %s.run()...", module_path)
    asyncio.run(worker_module.run())


if __name__ == "__main__":
    main()
