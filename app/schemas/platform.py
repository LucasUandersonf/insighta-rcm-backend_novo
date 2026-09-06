"""
app/schemas/platform.py — painel interno de Customer Success (ver
DECISÃO completa em app/sql/026_platform_customer_success.sql e
app/api/platform_admin_auth.py).
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr


class PlatformLoginRequest(BaseModel):
    email: EmailStr
    password: str


class PlatformLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class PlatformAuditLogEntryResponse(BaseModel):
    id: int
    actor_email: str
    action: str
    created_at: datetime

    model_config = {"from_attributes": True}


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
    # Decomposição de events_last_30d por recurso (pacientes, agenda,
    # faturamento, recurso de glosa, contratos, usuários) — mede MUTAÇÃO,
    # não navegação/leitura (ver DECISÃO em
    # app/sql/030_platform_feature_usage.sql). Orienta priorização de
    # backlog: o frontend soma esta mesma estrutura entre todos os
    # tenants para montar o ranking "recursos mais usados na plataforma".
    feature_usage_last_30d: dict[str, int]
    # Calculados em PlatformReportingService a partir dos campos acima —
    # nunca vêm direto do banco (ver DECISÃO no service sobre a régua de
    # classificação, deliberadamente simples nesta v1 e fácil de ajustar
    # sem tocar em SQL/migration).
    days_since_last_activity: int | None
    engagement_status: str

    model_config = {"from_attributes": True}


class PlatformAlertRunResponse(BaseModel):
    """Resultado de uma execução de PlatformAlertService — nomes de
    clínica (não ids) porque quem lê isto é sempre um humano da equipe,
    disparando manualmente via POST /platform/alerts/run ou lendo o log
    do job agendado."""

    new_alerts: list[str]
    reminders_sent: list[str]
    recovered: list[str]
