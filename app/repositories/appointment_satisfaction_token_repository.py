"""Repositório de core.appointment_satisfaction_tokens — tabela SEM RLS
(ver DECISÃO em app/models/appointment_satisfaction_token.py e
app/sql/052_appointment_satisfaction.sql). Opera sempre sob uma sessão
"crua" (get_db_no_tenant) no fluxo público, nunca sob uma sessão
tenant-aware — mesmo padrão de PasswordResetTokenRepository."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment_satisfaction_token import AppointmentSatisfactionToken


class AppointmentSatisfactionTokenRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, token: AppointmentSatisfactionToken) -> AppointmentSatisfactionToken:
        self.session.add(token)
        await self.session.flush()
        return token

    async def get_valid_by_hash(self, token_hash: str) -> AppointmentSatisfactionToken | None:
        """Só devolve o token se ainda não foi usado e não expirou — mesmo
        raciocínio de PasswordResetTokenRepository.get_valid_by_hash: não
        diferencia "não existe" de "expirado" de "já usado" para quem
        chama, tudo vira a mesma mensagem genérica de link inválido."""
        stmt = select(AppointmentSatisfactionToken).where(
            AppointmentSatisfactionToken.token_hash == token_hash,
            AppointmentSatisfactionToken.used_at.is_(None),
            AppointmentSatisfactionToken.expires_at > datetime.now(timezone.utc),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def mark_used(self, token: AppointmentSatisfactionToken) -> None:
        token.used_at = datetime.now(timezone.utc)
        await self.session.flush()
