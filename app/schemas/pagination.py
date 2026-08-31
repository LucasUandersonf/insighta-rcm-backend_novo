"""
app/schemas/pagination.py

Envelope genérico de paginação — não existia nenhum padrão equivalente
no projeto antes deste arquivo (grep em app/schemas/ por
`PaginatedResponse`/`total: int`/`offset: int` não encontrou nada:
os list endpoints existentes até agora devolviam `list[X]` "nu", sem
contagem total nem paginação real — patients.py já aceitava
limit/offset mas descartava o total). Usado pelo endpoint de audit log
(novo) e pelos três list endpoints que ganharam paginação nesta mudança
(contracts, denial-appeals, patients) — ver docstring de cada endpoint
para a forma exata da resposta.
"""
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int
