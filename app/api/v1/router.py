"""
app/api/v1/router.py — agrega todos os routers de endpoints em um único
APIRouter, montado no main.py sob o prefixo /api/v1. Cada novo domínio
(patients, appointments, contracts, marketing) ganha seu próprio arquivo
em endpoints/ e uma linha aqui — mantém main.py enxuto conforme o produto
cresce.
"""
from fastapi import APIRouter

from app.api.v1.endpoints import (
    analytics,
    appointments,
    audit_log,
    auth,
    billing,
    capacity,
    contracts,
    denial_appeals,
    faturas,
    guias,
    ingestion,
    insurance_companies,
    integrations,
    lotes,
    patients,
    professionals,
    report_recipients,
    reports,
    tenant,
    users,
    webhooks,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(tenant.router)
api_router.include_router(integrations.router)
api_router.include_router(analytics.router)
api_router.include_router(billing.router)
api_router.include_router(patients.router)
api_router.include_router(appointments.router)
api_router.include_router(contracts.router)
api_router.include_router(insurance_companies.router)
api_router.include_router(denial_appeals.router)
api_router.include_router(guias.router)
api_router.include_router(lotes.router)
api_router.include_router(faturas.router)
api_router.include_router(webhooks.router)
api_router.include_router(professionals.router)
api_router.include_router(capacity.router)
api_router.include_router(ingestion.router)
api_router.include_router(reports.router)
api_router.include_router(report_recipients.router)
api_router.include_router(audit_log.router)
