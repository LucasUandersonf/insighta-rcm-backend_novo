"""
app/services/public_satisfaction_service.py

Recebe o resto de avaliação de satisfação pós-atendimento (ver DECISÃO
completa em app/sql/052_appointment_satisfaction.sql) — o lado PÚBLICO,
sem autenticação, do fluxo cujo lado autenticado (gerar o link) vive em
AppointmentService.generate_satisfaction_link.

Mesmo padrão de AuthService: recebe uma sessão SEM tenant
(get_db_no_tenant), porque o único dado disponível no início é o token
opaco em si — o tenant_id só é conhecido DEPOIS de resolver o token.
Gravar a nota em core.appointments (tabela COM RLS) exige então abrir,
pontualmente, uma segunda sessão tenant-aware (get_db_with_tenant).
"""
import hashlib

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_with_tenant
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.appointment_satisfaction_token_repository import AppointmentSatisfactionTokenRepository
from app.schemas.appointment_satisfaction import PublicSatisfactionStatusResponse

_INVALID_LINK_DETAIL = "Link de avaliação inválido ou expirado."


class PublicSatisfactionService:
    def __init__(self, no_tenant_db: AsyncSession):
        self.no_tenant_db = no_tenant_db
        self.token_repo = AppointmentSatisfactionTokenRepository(no_tenant_db)

    async def get_status(self, token: str) -> PublicSatisfactionStatusResponse:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        record = await self.token_repo.get_valid_by_hash(token_hash)
        return PublicSatisfactionStatusResponse(valid=record is not None)

    async def submit_score(self, token: str, score: int) -> None:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        record = await self.token_repo.get_valid_by_hash(token_hash)
        if record is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_INVALID_LINK_DETAIL)

        async for tenant_db in get_db_with_tenant(str(record.tenant_id)):
            appointment_repo = AppointmentRepository(tenant_db)
            appointment = await appointment_repo.get_by_id(record.appointment_id)
            if appointment is None:
                # Agendamento apagado entre a geração do link e a resposta
                # do paciente — mesmo tratamento genérico de "link inválido"
                # do resto do fluxo, sem detalhar o motivo exato.
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_INVALID_LINK_DETAIL)
            appointment.visit_satisfaction_score = score
            await appointment_repo.save(appointment)

        await self.token_repo.mark_used(record)
        await self.no_tenant_db.commit()
