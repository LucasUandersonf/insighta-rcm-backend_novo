"""
app/services/contract_intake_service.py

Orquestra o fluxo de 3 passos do Parser Inteligente de Contratos, cada
um um endpoint HTTP separado (upload -> extract -> homologate) porque
"extração por IA" pode levar segundos e o front precisa mostrar a Tela
de Conferência ANTES de decidir persistir qualquer coisa — ver DECISÃO
em contract_extraction_service.py ("IA é o PRIMEIRO passo, nunca o
ÚLTIMO").

  1) create_draft   — sobe o PDF pro S3, cria o cabeçalho Contract com
                       status='rascunho'. Nenhum item ainda.
  2) run_extraction — baixa o PDF de volta do S3, extrai texto, chama a
                       IA, devolve PREVIEW (não grava contract_items).
                       Marca status='em_revisao' + extracted_at, porque
                       "já foi extraído, aguardando humano" é um estado
                       de negócio real (aparece na lista de contratos
                       pendentes de revisão), mesmo sem itens persistidos.
  3) homologate     — recebe a lista REVISADA pelo humano (pode ser
                       igual ou diferente da extração), grava via
                       ContractItemRepository.replace_items, marca
                       status='homologado' + homologated_by/at. só a
                       partir daqui o contrato "existe" para o motor de
                       glosa (ver ContractItemRepository.find_agreed_price).
"""
import uuid
from datetime import date, datetime, timezone

from fastapi import HTTPException, status

from app.models.contract import Contract
from app.repositories.contract_item_repository import ContractItemRepository
from app.repositories.contract_repository import ContractRepository
from app.schemas.contract import (
    ContractItemInput,
    ContractItemResponse,
    ContractResponse,
    ExtractedItemResponse,
    ExtractionPreviewResponse,
)
from app.services.contract_extraction_service import (
    AnthropicContractExtractor,
    ContractExtractionError,
    detect_price_anomalies,
)
from app.services.contract_pdf_text import ContractPdfTextError, extract_text
from app.services.contract_storage_client import ContractStorageClient, ContractStorageError, build_pdf_key


class ContractIntakeService:
    def __init__(self, repo: ContractRepository, item_repo: ContractItemRepository):
        self.repo = repo
        self.item_repo = item_repo

    async def create_draft(
        self,
        *,
        tenant_id: str,
        insurance_plan_id: uuid.UUID,
        valid_from: date,
        valid_until: date | None,
        filename: str,
        pdf_bytes: bytes,
    ) -> ContractResponse:
        tenant_uuid = uuid.UUID(tenant_id)
        contract = Contract(
            id=uuid.uuid4(),
            tenant_id=tenant_uuid,
            insurance_plan_id=insurance_plan_id,
            valid_from=valid_from,
            valid_until=valid_until,
            status="rascunho",
        )

        try:
            storage = ContractStorageClient()
        except ContractStorageError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

        pdf_key = build_pdf_key(tenant_id, contract.id, filename)
        try:
            await storage.upload_pdf(key=pdf_key, pdf_bytes=pdf_bytes)
        except Exception as exc:  # boto3 lança tipos variados de erro de rede/credencial
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Falha ao subir o PDF para o storage: {exc}"
            ) from exc

        contract.pdf_s3_key = pdf_key
        saved = await self.repo.add(contract)
        return _to_response(saved, [])

    async def run_extraction(self, contract_id: uuid.UUID) -> ExtractionPreviewResponse:
        contract = await self._get_or_404(contract_id)
        if contract.pdf_s3_key is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Este contrato não tem PDF associado — não há o que extrair.",
            )

        try:
            storage = ContractStorageClient()
            pdf_bytes = await storage.download_pdf(key=contract.pdf_s3_key)
        except ContractStorageError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Falha ao baixar o PDF do storage: {exc}"
            ) from exc

        try:
            pdf_text = extract_text(pdf_bytes)
        except ContractPdfTextError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

        try:
            extractor = AnthropicContractExtractor()
            result = await extractor.extract(pdf_text)
        except ContractExtractionError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

        # Alerta de anomalia de preço: aritmética determinística contra o
        # contrato homologado anterior do mesmo plano, não IA (ver
        # DECISÃO em contract_extraction_service.detect_price_anomalies).
        previous_items = await self.item_repo.list_items_for_previous_homologated_contract(
            contract.insurance_plan_id, exclude_contract_id=contract.id
        )
        previous_prices = {i.tuss_code: i.agreed_price for i in previous_items}
        result.warnings.extend(detect_price_anomalies(result.items, previous_prices))

        contract.status = "em_revisao"
        contract.extracted_at = datetime.now(timezone.utc)
        await self.repo.save(contract)

        return ExtractionPreviewResponse(
            contract_id=contract.id,
            status=contract.status,
            items=[
                ExtractedItemResponse(
                    tuss_code=i.tuss_code, procedure_name=i.procedure_name, agreed_price=i.agreed_price, warning=i.warning
                )
                for i in result.items
            ],
            warnings=result.warnings,
        )

    async def homologate(
        self, *, tenant_id: str, contract_id: uuid.UUID, homologated_by: uuid.UUID, items: list[ContractItemInput]
    ) -> ContractResponse:
        contract = await self._get_or_404(contract_id)

        saved_items = await self.item_repo.replace_items(
            tenant_id=uuid.UUID(tenant_id),
            contract_id=contract.id,
            items=[item.model_dump() for item in items],
        )

        contract.status = "homologado"
        contract.homologated_by = homologated_by
        contract.homologated_at = datetime.now(timezone.utc)
        await self.repo.save(contract)

        return _to_response(contract, saved_items)

    async def _get_or_404(self, contract_id: uuid.UUID) -> Contract:
        contract = await self.repo.get_by_id(contract_id)
        if contract is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contrato não encontrado neste tenant.")
        return contract


def _to_response(contract: Contract, items: list) -> ContractResponse:
    # Mesma serialização de ContractService._to_response — função solta
    # (em vez de importar a classe) só para não criar dependência
    # circular entre os dois módulos de serviço por causa de um helper
    # puro de formatação.
    return ContractResponse(
        id=contract.id,
        insurance_plan_id=contract.insurance_plan_id,
        valid_from=contract.valid_from,
        valid_until=contract.valid_until,
        status=contract.status,
        pdf_s3_key=contract.pdf_s3_key,
        items=[ContractItemResponse.model_validate(i) for i in items],
        created_at=contract.created_at,
    )
