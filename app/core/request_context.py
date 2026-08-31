"""
app/core/request_context.py

ContextVar para carregar o request_id por toda a árvore de chamadas de
uma requisição, sem precisar passar explicitamente por parâmetro em
cada função — funciona corretamente sob asyncio porque ContextVar é
isolado por task, não é uma variável global compartilhada entre
requisições concorrentes (diferente de uma variável de módulo comum,
que vazaria entre requests simultâneos).

DECISÃO — por que isso importa quando é 1 desenvolvedor sustentando
produção sozinho
-------------------------------------------------------------------------
O tempo entre "usuário relata um problema" e "eu entendo o que
aconteceu" é o gargalo real de suporte numa operação de uma pessoa só.
O request_id é o elo entre as duas pontas: aparece na resposta de erro
que o usuário vê (ele pode citar isso numa mensagem de suporte) e
aparece em toda linha de log gerada durante aquele request — filtrar
"me dá tudo desse request_id" substitui "manda print da tela" +
vasculhar log por horário aproximado.
"""
import uuid
from contextvars import ContextVar

_request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def set_request_id(value: str | None = None) -> str:
    request_id = value or str(uuid.uuid4())
    _request_id_var.set(request_id)
    return request_id


def get_request_id() -> str:
    return _request_id_var.get()
