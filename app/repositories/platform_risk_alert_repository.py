"""
app/repositories/platform_risk_alert_repository.py — ver DECISÃO completa
em app/sql/027_platform_risk_alerts.sql. Recebe uma sessão SEM tenant
(get_db_no_tenant) — mesma exigência de app/services/auth_service.py: a
tabela por trás não tem RLS, e o chamador (PlatformAlertService) precisa
enxergar/alterar o episódio de risco de QUALQUER clínica, não de uma só.
"""
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform_risk_alert import PlatformRiskAlert


class PlatformRiskAlertRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, tenant_id: uuid.UUID) -> PlatformRiskAlert | None:
        stmt = select(PlatformRiskAlert).where(PlatformRiskAlert.tenant_id == tenant_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def open_episode(self, tenant_id: uuid.UUID, *, now: datetime) -> None:
        """Novo episódio de risco — só chamado quando `get()` já confirmou
        que não existe um em aberto para este tenant."""
        self.session.add(PlatformRiskAlert(tenant_id=tenant_id, first_detected_at=now, last_alert_sent_at=now))
        await self.session.flush()

    async def touch(self, alert: PlatformRiskAlert, *, now: datetime) -> None:
        """Reenvio de lembrete sobre o MESMO episódio (ver
        PlatformAlertService._RE_ALERT_INTERVAL_DAYS) — `first_detected_at`
        não muda, só quando o último aviso saiu."""
        alert.last_alert_sent_at = now
        await self.session.flush()

    async def close_episode(self, alert: PlatformRiskAlert) -> None:
        """A clínica saiu do status "risco" — apaga o episódio para que
        uma entrada FUTURA em risco conte como um alerta novo, não um
        reenvio do mesmo."""
        await self.session.delete(alert)
        await self.session.flush()
