"""Mesmo padrão de patient_repository.py: sem WHERE tenant_id manual — o
RLS, sob a sessão tenant-aware injetada pelo endpoint, já garante isso."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self) -> list[User]:
        stmt = select(User).order_by(User.full_name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        stmt = select(User).where(User.id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        # CITEXT na coluna (ver 001_init_schema.sql) já resolve
        # case-insensitivity no próprio banco — comparação direta aqui é
        # suficiente, sem precisar de .lower() na aplicação.
        stmt = select(User).where(User.email == email)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def add(self, user: User) -> User:
        self.session.add(user)
        await self.session.flush()
        return user

    async def save(self, user: User) -> User:
        """Persiste alterações em um objeto já rastreado pela sessão
        (obtido via get_by_id) — usado pelos fluxos de update/reset de
        senha, onde o service só muda atributos e precisa comitar."""
        await self.session.flush()
        return user
