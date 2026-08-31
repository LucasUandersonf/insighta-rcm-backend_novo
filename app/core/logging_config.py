"""
app/core/logging_config.py

DECISÃO — logging estruturado em JSON, não texto livre
-------------------------------------------------------------------------
`logging.basicConfig` (o que o projeto usava até agora nos workers)
produz texto livre — bom para ler no terminal na hora, péssimo para
buscar depois. Em produção, os logs vão para CloudWatch Logs (ou
equivalente); JSON estruturado permite filtrar por campo
(`request_id`, `tenant_id`, `status_code`) direto na ferramenta de log,
em vez de grep por string e esperança. Cada linha já sai com o
request_id do contexto atual (ver request_context.py), então uma
investigação de bug em produção começa com "todas as linhas desse
request_id", não com "por volta de que horas foi isso?".
"""
import json
import logging
import sys
from datetime import datetime, timezone

from app.core.request_context import get_request_id

_EXTRA_FIELDS = ("tenant_id", "user_id", "path", "method", "status_code", "duration_ms")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": get_request_id(),
        }
        for key in _EXTRA_FIELDS:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(environment: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO if environment == "production" else logging.DEBUG)
    # Bibliotecas de terceiros são barulhentas em DEBUG — nível mais alto
    # para elas, para não afogar o log que a aplicação de fato gera.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
