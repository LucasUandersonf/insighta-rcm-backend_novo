"""
app/services/denial_appeal_service.py

Orquestra o ciclo de vida do Recurso de Glosa: aberto -> protocolado ->
deferido|indeferido -> (opcional) nip_aberta. Ver DECISÃO completa em
app/sql/008_denial_appeals.sql.
"""
import uuid
from datetime import date, datetime, timezone

from fastapi import HTTPException, status

from app.core.config import get_settings
from app.models.denial_appeal import DenialAppeal, DenialAppealAttachment
from app.repositories.billing_repository import BillingRepository
from app.repositories.denial_appeal_attachment_repository import DenialAppealAttachmentRepository
from app.repositories.denial_appeal_repository import DenialAppealRepository
from app.repositories.insurance_company_repository import InsuranceCompanyRepository
from app.repositories.insurance_plan_repository import InsurancePlanRepository
from app.repositories.tenant_repository import TenantRepository
from app.schemas.denial_appeal import (
    DenialAppealAttachmentResponse,
    DenialAppealCreateRequest,
    DenialAppealResolveRequest,
    DenialAppealResponse,
)
from app.services.appeal_deadline_calculator import compute_deadline
from app.services.appeal_storage_client import AppealStorageClient, AppealStorageError, build_attachment_key
from app.services.denial_appeal_pdf_builder import DenialAppealDocumentContext, build_denial_appeal_pdf

settings = get_settings()

# aberto -> protocolado é a única transição de "file". As demais
# (deferido/indeferido/nip_aberta) são todas alcançáveis a partir de
# protocolado OU de nip_aberta (uma NIP pode ser resolvida depois),
# nunca a partir de 'aberto' direto — não faz sentido a operadora
# responder um recurso que a clínica nem protocolou ainda.
#
# BUG CORRIGIDO — 'indeferido' faltava nesta lista: o próprio model
# (ver DECISÃO em app/models/denial_appeal.py) documenta o ciclo como
# "aberto -> protocolado -> deferido|indeferido -> (opcional) nip_aberta",
# ou seja, uma negativa da operadora (indeferido) NÃO é terminal por si
# só — pode ser escalada para NIP depois. Sem 'indeferido' aqui, essa
# escalada sempre voltava 409 mesmo sendo o fluxo documentado como
# válido. 'deferido' continua de fora de propósito: um recurso DEFERIDO
# (a clínica ganhou) não tem o que escalar para a ANS.
_RESOLVABLE_FROM = ("protocolado", "indeferido", "nip_aberta")


class DenialAppealService:
    def __init__(
        self,
        repo: DenialAppealRepository,
        attachment_repo: DenialAppealAttachmentRepository,
        billing_repo: BillingRepository,
        plan_repo: InsurancePlanRepository,
        company_repo: InsuranceCompanyRepository,
        tenant_repo: TenantRepository,
    ):
        self.repo = repo
        self.attachment_repo = attachment_repo
        self.billing_repo = billing_repo
        self.plan_repo = plan_repo
        self.company_repo = company_repo
        self.tenant_repo = tenant_repo

    async def _resolve_deadline_days(self, insurance_plan_id: uuid.UUID) -> int | None:
        plan = await self.plan_repo.get_by_id(insurance_plan_id)
        if plan is None or plan.insurance_company_id is None:
            return None
        company = await self.company_repo.get_by_id(plan.insurance_company_id)
        return company.default_appeal_deadline_days if company else None

    async def create_appeal(self, tenant_id: str, created_by: uuid.UUID, data: DenialAppealCreateRequest) -> DenialAppealResponse:
        billing = await self.billing_repo.get_by_id(data.billing_id)
        if billing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Faturamento não encontrado neste tenant.")

        deadline_at = data.deadline_at
        if deadline_at is None:
            company_days = await self._resolve_deadline_days(billing.insurance_plan_id)
            deadline_at = compute_deadline(
                data.denied_at,
                company_deadline_days=company_days,
                default_deadline_days=settings.DEFAULT_APPEAL_DEADLINE_DAYS,
            )

        appeal = DenialAppeal(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            billing_id=data.billing_id,
            appeal_type=data.appeal_type,
            operator_denial_reason=data.operator_denial_reason,
            denied_at=data.denied_at,
            deadline_at=deadline_at,
            status="aberto",
            created_by=created_by,
        )
        saved = await self.repo.add(appeal)
        return self._to_response(saved, [])

    async def list_appeals(self, *, status_filter: str | None = None) -> list[DenialAppealResponse]:
        appeals = await self.repo.list_all(status_filter=status_filter)
        result = []
        for appeal in appeals:
            attachments = await self.attachment_repo.list_by_appeal(appeal.id)
            result.append(self._to_response(appeal, attachments))
        return result

    async def list_appeals_paginated(
        self, *, limit: int, offset: int, status_filter: str | None = None
    ) -> tuple[list[DenialAppealResponse], int]:
        appeals, total = await self.repo.list_paginated(limit=limit, offset=offset, status_filter=status_filter)
        result = []
        for appeal in appeals:
            attachments = await self.attachment_repo.list_by_appeal(appeal.id)
            result.append(self._to_response(appeal, attachments))
        return result, total

    async def get_appeal(self, appeal_id: uuid.UUID) -> DenialAppealResponse:
        appeal = await self._get_or_404(appeal_id)
        attachments = await self.attachment_repo.list_by_appeal(appeal.id)
        return self._to_response(appeal, attachments)

    async def count_due_within(self, *, as_of: date, horizon_days: int) -> int:
        return await self.repo.count_due_within(as_of=as_of, horizon_days=horizon_days)

    async def file_appeal(self, appeal_id: uuid.UUID, filed_at: datetime | None) -> DenialAppealResponse:
        appeal = await self._get_or_404(appeal_id)
        if appeal.status != "aberto":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Recurso está em status '{appeal.status}' — só é possível protocolar a partir de 'aberto'.",
            )
        appeal.status = "protocolado"
        appeal.filed_at = filed_at or datetime.now(timezone.utc)
        await self.repo.save(appeal)
        attachments = await self.attachment_repo.list_by_appeal(appeal.id)
        return self._to_response(appeal, attachments)

    async def resolve_appeal(self, appeal_id: uuid.UUID, data: DenialAppealResolveRequest) -> DenialAppealResponse:
        appeal = await self._get_or_404(appeal_id)
        if appeal.status not in _RESOLVABLE_FROM:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Recurso está em status '{appeal.status}' — só é possível registrar deferido/indeferido/NIP "
                    "a partir de 'protocolado' (ou reabrir uma NIP em andamento)."
                ),
            )
        appeal.status = data.status
        appeal.resolution_notes = data.resolution_notes
        # 'nip_aberta' não é uma resolução final (é uma ESCALADA — o caso
        # continua em aberto, agora na ANS), então só marca resolved_at
        # quando de fato terminou (deferido/indeferido).
        if data.status in ("deferido", "indeferido"):
            appeal.resolved_at = datetime.now(timezone.utc)
        await self.repo.save(appeal)
        attachments = await self.attachment_repo.list_by_appeal(appeal.id)
        return self._to_response(appeal, attachments)

    async def upload_attachment(
        self, *, tenant_id: str, appeal_id: uuid.UUID, uploaded_by: uuid.UUID, filename: str, content: bytes, content_type: str
    ) -> DenialAppealAttachmentResponse:
        appeal = await self._get_or_404(appeal_id)

        try:
            storage = AppealStorageClient()
        except AppealStorageError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

        key = build_attachment_key(tenant_id, appeal.id, filename)
        try:
            await storage.upload_file(key=key, content=content, content_type=content_type)
        except Exception as exc:  # boto3 lança tipos variados de erro de rede/credencial
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Falha ao subir o anexo para o storage: {exc}"
            ) from exc

        attachment = DenialAppealAttachment(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            appeal_id=appeal.id,
            s3_key=key,
            filename=filename,
            uploaded_by=uploaded_by,
        )
        saved = await self.attachment_repo.add(attachment)
        return DenialAppealAttachmentResponse.model_validate(saved)

    async def list_attachments(self, appeal_id: uuid.UUID) -> list[DenialAppealAttachmentResponse]:
        await self._get_or_404(appeal_id)
        attachments = await self.attachment_repo.list_by_appeal(appeal_id)
        return [DenialAppealAttachmentResponse.model_validate(a) for a in attachments]

    async def build_appeal_document(self, tenant_id: str, appeal_id: uuid.UUID, justification: str | None) -> bytes:
        """
        Gera o RASCUNHO em PDF do documento de recurso — ver DECISÃO
        completa em denial_appeal_pdf_builder.py (dados factuais
        pré-preenchidos, justificativa de mérito fica a cargo do
        usuário). `_get_or_404` primeiro pelo mesmo motivo de sempre:
        nunca revelar via 404-vs-outro-erro se um appeal_id de outro
        tenant existe (RLS já impede o SELECT de enxergar, mas o
        contrato de erro fica consistente com o resto do produto).
        """
        await self._get_or_404(appeal_id)
        context_row = await self.repo.get_document_context(appeal_id)
        assert context_row is not None  # _get_or_404 já confirmou que o appeal existe

        tenant = await self.tenant_repo.get_by_id(uuid.UUID(tenant_id))
        assert tenant is not None  # tenant_id vem de um JWT já validado

        context = DenialAppealDocumentContext(
            tenant_legal_name=tenant.legal_name,
            tenant_cnpj=tenant.cnpj,
            appeal_type=context_row["appeal_type"],
            operator_denial_reason=context_row["operator_denial_reason"],
            denied_at=context_row["denied_at"],
            deadline_at=context_row["deadline_at"],
            insurance_plan_name=context_row["insurance_plan_name"],
            patient_name=context_row["patient_name"],
            patient_cpf=context_row["patient_cpf"],
            professional_name=context_row["professional_name"],
            professional_registry=context_row["professional_registry"],
            procedure_code=context_row["procedure_code"],
            cid_code=context_row["cid_code"],
            service_date=context_row["service_date"],
            charged_value=float(context_row["charged_value"]),
            guia_tipo=context_row["guia_tipo"],
            guia_numero=context_row["guia_numero"],
            guia_senha=context_row["guia_senha"],
            justification=justification,
        )
        return build_denial_appeal_pdf(context)

    async def _get_or_404(self, appeal_id: uuid.UUID) -> DenialAppeal:
        appeal = await self.repo.get_by_id(appeal_id)
        if appeal is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recurso não encontrado neste tenant.")
        return appeal

    @staticmethod
    def _to_response(appeal: DenialAppeal, attachments: list) -> DenialAppealResponse:
        return DenialAppealResponse(
            id=appeal.id,
            billing_id=appeal.billing_id,
            appeal_type=appeal.appeal_type,
            operator_denial_reason=appeal.operator_denial_reason,
            denied_at=appeal.denied_at,
            deadline_at=appeal.deadline_at,
            status=appeal.status,
            filed_at=appeal.filed_at,
            resolution_notes=appeal.resolution_notes,
            resolved_at=appeal.resolved_at,
            created_at=appeal.created_at,
            attachments=[DenialAppealAttachmentResponse.model_validate(a) for a in attachments],
        )
