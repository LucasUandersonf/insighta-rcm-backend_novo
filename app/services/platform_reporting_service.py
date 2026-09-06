"""
app/services/platform_reporting_service.py — painel interno de Customer
Success (ver DECISÃO completa em app/sql/026_platform_customer_success.sql).

DECISÃO — régua de engajamento é uma heurística SIMPLES de v1, no Python
-------------------------------------------------------------------------
Classificar "engajado" vs. "em risco" é, na prática, uma decisão de
negócio que vai mudar conforme a equipe de Customer Success aprender o
que de fato prediz cancelamento (ex: um cliente sazonal pode ficar 20
dias sem logar por motivo legítimo). Mantendo a régua aqui, em Python, em
vez de embutida na função SQL, qualquer ajuste futuro (mudar o limite de
dias, pesar mais um tipo de evento que outro) não exige nova migration —
só mexer neste arquivo.
"""
from datetime import datetime, timezone

from app.repositories.platform_reporting_repository import PlatformReportingRepository, TenantUsageRow
from app.schemas.platform import TenantUsageSummary

# Tenant criado há menos dias que isto ainda está no "período de
# implantação" — zero atividade não é sinal de risco, é o normal de quem
# acabou de assinar e ainda está em onboarding/migração de dados.
_NEW_TENANT_GRACE_DAYS = 7

# Abaixo disto em eventos de auditoria nos últimos 30 dias, mesmo um
# tenant "veterano" está com uso baixo o suficiente para merecer atenção
# — não necessariamente cancelamento iminente, mas vale contato proativo.
_LOW_ENGAGEMENT_THRESHOLD = 5


def _classify_engagement(row: TenantUsageRow, *, now: datetime) -> tuple[str, int | None]:
    days_since_last_activity = None
    if row.last_activity_at is not None:
        days_since_last_activity = (now - row.last_activity_at).days

    tenant_age_days = (now - row.tenant_created_at).days

    if not row.tenant_is_active:
        status = "inativo"
    elif tenant_age_days < _NEW_TENANT_GRACE_DAYS:
        status = "novo"
    elif row.events_last_30d == 0:
        status = "risco"
    elif row.events_last_30d < _LOW_ENGAGEMENT_THRESHOLD:
        status = "atencao"
    else:
        status = "engajado"

    return status, days_since_last_activity


class PlatformReportingService:
    def __init__(self, repo: PlatformReportingRepository):
        self.repo = repo

    async def list_tenant_usage(self) -> list[TenantUsageSummary]:
        rows = await self.repo.list_tenant_usage()
        now = datetime.now(timezone.utc)
        summaries = []
        for row in rows:
            engagement_status, days_since_last_activity = _classify_engagement(row, now=now)
            summaries.append(
                TenantUsageSummary(
                    tenant_id=row.tenant_id,
                    trade_name=row.trade_name,
                    plan_tier=row.plan_tier,
                    tenant_is_active=row.tenant_is_active,
                    tenant_created_at=row.tenant_created_at,
                    active_users=row.active_users,
                    last_activity_at=row.last_activity_at,
                    events_last_30d=row.events_last_30d,
                    patients_total=row.patients_total,
                    appointments_last_30d=row.appointments_last_30d,
                    billings_last_30d=row.billings_last_30d,
                    days_since_last_activity=days_since_last_activity,
                    engagement_status=engagement_status,
                )
            )
        # Risco primeiro — é a lista que a equipe de Customer Success
        # precisa olhar sem precisar reordenar na mão.
        _order = {"risco": 0, "atencao": 1, "novo": 2, "engajado": 3, "inativo": 4}
        summaries.sort(key=lambda s: (_order.get(s.engagement_status, 99), s.trade_name))
        return summaries
