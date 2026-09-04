"""
app/core/aws_s3.py

Kwargs compartilhados de conexão S3 para os três storage clients HTTP
síncronos (ingestion_storage_client.py, contract_storage_client.py,
appeal_storage_client.py) — cada um mantém seu próprio bucket/lógica de
domínio (ver DECISÃO em cada arquivo sobre por que não se misturam);
isto aqui é só a fiação de CONEXÃO, que é idêntica nos três, extraída
para não divergir se precisar mudar em um lugar só.

DECISÃO — AWS_S3_ENDPOINT_URL existe para apontar para um serviço
S3-compatível (ex: MinIO) em ambiente SEM AWS de verdade — o caso
concreto que motivou isto: testar a Central de Upload/Contratos pela
TELA num deploy de teste no Railway, que não tem um S3 nativo, antes de
uma conta AWS real estar provisionada. Em produção real esta variável
fica None/ausente e o boto3 resolve o endpoint real da AWS sozinho —
nenhum comportamento muda para quem já usa AWS de verdade.

Quando setado, força `addressing_style="path"` (bucket no PATH da URL:
`http://host:9000/meu-bucket/chave`, não em subdomínio
`https://meu-bucket.s3.amazonaws.com/chave`) — MinIO e a maioria dos
S3-compatíveis só aceitam essa forma. A AWS real aceita as duas, então
forçar path-style SÓ quando há endpoint customizado não quebra quem
usa AWS de verdade (nunca força path-style nesse caso).
"""
from botocore.config import Config

from app.core.config import get_settings

settings = get_settings()


def s3_client_kwargs() -> dict:
    if not settings.AWS_S3_ENDPOINT_URL:
        return {}
    return {
        "endpoint_url": settings.AWS_S3_ENDPOINT_URL,
        "config": Config(s3={"addressing_style": "path"}),
    }
