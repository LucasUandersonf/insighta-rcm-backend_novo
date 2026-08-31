"""
app/api/v1/endpoints/patients.py — mesmo padrão de billing.py:
DbSession (tenant-aware) -> Repository -> Service -> schema de saída.

NOTA — mudança de forma de resposta em GET /patients
-------------------------------------------------------------------
Antes devolvia `list[PatientResponse]` "nu" (limit/offset já existiam,
mas sem contagem total — o cliente não conseguia montar paginação de
verdade). Passa a devolver o envelope
`{items: PatientResponse[], total, limit, offset}` (mesmo formato usado
em GET /audit-log, GET /contracts/active e GET /denial-appeals — ver
app/schemas/pagination.py). Isso QUEBRA o contrato atual do frontend
(que hoje faz `apiClient.get<Patient[]>("/api/v1/patients")` esperando
um array bruto — ver src/pages/PatientsPage.tsx e
src/pages/AppointmentsPage.tsx) — o call site do frontend precisa ser
atualizado para ler `.items` em vez do array direto.
"""
from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.patient_repository import PatientRepository
from app.schemas.pagination import PaginatedResponse
from app.schemas.patient import PatientCreateRequest, PatientResponse
from app.services.patient_service import PatientService

router = APIRouter(prefix="/patients", tags=["patients"])

# 'atendimento' pode cadastrar pacientes (é o dia a dia da recepção);
# 'auditor' fica de fora por ser role somente-leitura.
_CAN_WRITE = ("atendimento", "admin", "owner")


@router.post("", response_model=PatientResponse, status_code=201)
async def create_patient(
    payload: PatientCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> PatientResponse:
    service = PatientService(PatientRepository(db))
    return await service.create_patient(current_user.tenant_id, payload)


@router.get("", response_model=PaginatedResponse[PatientResponse])
async def list_patients(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE, "financeiro", "auditor")),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[PatientResponse]:
    """Resposta: `{items: PatientResponse[], total, limit, offset}` — ver
    NOTA no topo do arquivo sobre a mudança de forma em relação à versão
    anterior deste endpoint."""
    service = PatientService(PatientRepository(db))
    items, total = await service.list_patients_paginated(limit=limit, offset=offset)
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)
