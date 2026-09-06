"""
app/schemas/platform.py — painel interno de Customer Success (ver
DECISÃO completa em app/sql/026_platform_customer_success.sql e
app/api/platform_admin_auth.py).
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class PlatformLoginRequest(BaseModel):
    password: str


class PlatformLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TenantUsageSummary(BaseModel):
    tenant_id: UUID
    trade_name: str
    plan_tier: str
    tenant_is_active: bool
    tenant_created_at: datetime
    active_users: int
    last_activity_at: datetime | None
    events_last_30d: int
    patients_total: int
    appointments_last_30d: int
    billings_last_30d: int
    # Calculados em PlatformReportingService a partir dos campos acima —
    # nunca vêm direto do banco (ver DECISÃO no service sobre a régua de
    # classificação, deliberadamente simples nesta v1 e fácil de ajustar
    # sem tocar em SQL/migration).
    days_since_last_activity: int | None
    engagement_status: str

    model_config = {"from_attributes": True}
