"""
app/api/v1/endpoints/public_satisfaction.py

Lado PÚBLICO (sem autenticação) do link de avaliação de satisfação
pós-atendimento — mesma classe de rota sensível a abuso que
/auth/password-reset/*, e mesmo remédio: rate limit + sessão sem tenant
(get_db_no_tenant), já que ninguém está logado neste ponto do fluxo.
Ver DECISÃO completa em app/sql/052_appointment_satisfaction.sql.
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.db.session import get_db_no_tenant
from app.schemas.appointment_satisfaction import PublicSatisfactionStatusResponse, PublicSatisfactionSubmitRequest
from app.services.public_satisfaction_service import PublicSatisfactionService

router = APIRouter(prefix="/public/satisfaction", tags=["public"])
settings = get_settings()


@router.get("/{token}", response_model=PublicSatisfactionStatusResponse)
@limiter.limit(settings.SATISFACTION_RATE_LIMIT)
async def get_satisfaction_status(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> PublicSatisfactionStatusResponse:
    """O frontend chama isso ao abrir a página do link para decidir entre
    mostrar o formulário de avaliação ou uma mensagem de link inválido/
    expirado, sem precisar tentar o POST primeiro."""
    return await PublicSatisfactionService(db).get_status(token)


@router.post("/{token}", status_code=204)
@limiter.limit(settings.SATISFACTION_RATE_LIMIT)
async def submit_satisfaction_score(
    request: Request,
    token: str,
    payload: PublicSatisfactionSubmitRequest,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> None:
    await PublicSatisfactionService(db).submit_score(token, payload.score)
