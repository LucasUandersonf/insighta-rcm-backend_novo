"""Repositório de core.password_reset_tokens — tabela SEM RLS (ver DECISÃO
em app/models/password_reset_token.py e app/sql/012_password_reset.sql).
Opera sempre sob uma sessão "crua" (get_db_no_tenant), nunca sob uma
sessão tenant-aware."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.password_reset_token import PasswordResetToken


class PasswordResetTokenRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, token: PasswordResetToken) -> PasswordResetToken:
        self.session.add(token)
        await self.session.flush()
        return token

    async def get_valid_by_hash(self, token_hash: str) -> PasswordResetToken | None:
        """Só devolve o token se ainda não foi usado e não expirou. Não
        diferencia "não existe" de "expirado" de "já usado" para quem
        chama — os três casos viram a mesma mensagem genérica de "link
        inválido ou expirado" no endpoint, para não dar pista a quem
        estiver testando tokens aleatórios."""
        stmt = select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > datetime.now(timezone.utc),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def mark_used(self, token: PasswordResetToken) -> None:
        token.used_at = datetime.now(timezone.utc)
        await self.session.flush()
