"""baseline: marca o schema criado manualmente por 001_init_schema.sql
   e 002_auth_resolver.sql como o ponto de partida do Alembic

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-27

DECISÃO ARQUITETURAL — Por que esta migration está VAZIA
-------------------------------------------------------------------------
As tabelas core.* já existem no banco porque foram criadas por SQL bruto
(001_init_schema.sql, incluindo RLS, e 002_auth_resolver.sql, com a
função SECURITY DEFINER) ANTES de introduzirmos o Alembic no projeto.
Isso foi intencional: RLS, FORCE ROW LEVEL SECURITY, funções SECURITY
DEFINER e roles de banco são o tipo de DDL que preferimos revisar e
aplicar manualmente/via script dedicado, com o texto completo visível e
auditável, em vez de depender de autogenerate do Alembic para acertar
detalhes de segurança tão sensíveis (o autogenerate do SQLAlchemy não
entende RLS nem SECURITY DEFINER — ele geraria um diff incompleto ou
incorreto para esses objetos).

Esta migration, portanto, não faz upgrade/downgrade nenhum — ela existe
só para ocupar a "revision 0001" na tabela core.alembic_version. O passo
de instalação em um banco que já rodou 001/002 manualmente é:

    alembic stamp 0001_baseline

`stamp` registra a revision como aplicada SEM executar upgrade() — ou
seja, "avisa" o Alembic que o banco já está neste estado, sem tentar
recriar as tabelas que já existem (o que falharia com "relation already
exists"). A partir daqui, toda mudança de schema nova (novas colunas,
novas tabelas, novos índices) é feita via `alembic revision --autogenerate`
normalmente, comparando o banco contra os models Python.

Em um ambiente NOVO (banco vazio, ex: CI de testes), a sequência correta é:
    1) psql -f app/sql/001_init_schema.sql   (cria tabelas + RLS)
    2) psql -f app/sql/002_auth_resolver.sql (cria função de login)
    3) alembic stamp 0001_baseline
    4) alembic upgrade head                  (aplica migrations futuras, se houver)
"""
from collections.abc import Sequence

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Intencionalmente vazio — ver docstring acima.
    pass


def downgrade() -> None:
    # Não fazemos downgrade de um baseline que representa DDL manual
    # sensível (RLS, SECURITY DEFINER). Reverter isso deve ser uma
    # decisão explícita e revisada, não um "alembic downgrade" automático.
    pass
