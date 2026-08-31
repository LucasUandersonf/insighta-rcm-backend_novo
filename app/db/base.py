"""
app/db/base.py

Base declarativa única do SQLAlchemy. Todo model em app/models/ herda
desta classe. Mantê-la em um módulo próprio (em vez de dentro de
session.py) evita import circular quando o Alembic precisar importar
todos os models para autogerar migrations (Base.metadata precisa
"conhecer" todas as tabelas).
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
