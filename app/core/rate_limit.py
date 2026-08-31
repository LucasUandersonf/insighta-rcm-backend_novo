"""
app/core/rate_limit.py

Instância ÚNICA e compartilhada de Limiter (slowapi). Correção de um
descuido: ter um Limiter() criado em main.py E outro em cada endpoint
(como auth.py fazia antes) resulta em dois contadores de estado
independentes — um limite de "5/minute" no login não teria efeito real
nenhum sobre o limite default global, e vice-versa, porque cada Limiter
mantém seu próprio armazenamento interno de contagem. Um único objeto,
importado onde for necessário, é o que garante que
`app.state.limiter` (usado pelo exception handler em main.py) e os
decorators `@limiter.limit(...)` nos endpoints estejam contando a
MESMA coisa.

DECISÃO — storage_uri configurável, com fallback em memória
-------------------------------------------------------------------------
Sem `storage_uri`, o slowapi guarda a contagem de requisições em memória
DO PROCESSO. Isso funciona hoje (uma única instância da aplicação), mas
quebra silenciosamente assim que houver mais de uma instância atrás de
um load balancer — cada instância teria seu próprio contador, e um
atacante (ou só um cliente com bug no retry) poderia efetivamente
multiplicar o limite real pelo número de instâncias, sem nenhum erro
aparecer. Setar `RATE_LIMIT_STORAGE_URI` (ex: `redis://...`) resolve
isso trocando o backend de contagem para um armazenamento compartilhado
entre instâncias — é uma troca de variável de ambiente, não de código,
quando a hora chegar.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

settings = get_settings()

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.RATE_LIMIT_DEFAULT],
    # Só passa storage_uri quando configurado — deixa o slowapi usar seu
    # próprio padrão (memória) quando None, em vez de arriscar passar
    # explicitamente um valor que a biblioteca não espera.
    **({"storage_uri": settings.RATE_LIMIT_STORAGE_URI} if settings.RATE_LIMIT_STORAGE_URI else {}),
)
