"""
tests/integration/test_contract_extraction_reconciliation.py

Teste de RECONCILIAÇÃO fim a fim do Parser Inteligente de Contratos
(POST /contracts/upload -> POST /contracts/{id}/extract -> POST
/contracts/{id}/homologate), a outra metade de "inclusão de documentos"
que test_ingestion_reconciliation.py não cobre.

Sobe um PDF DE VERDADE (texto real extraível via pypdf — gerado com
reportlab só para este teste, não um fixture de bytes hardcoded),
passa por uma extração de IA FALSA (o único jeito de testar isso sem
uma ANTHROPIC_API_KEY real neste sandbox — mesma técnica de "mockar só
a fronteira de rede" de test_ingestion_reconciliation.py/
test_ingestion_upload.py: só ContractStorageClient.upload_pdf/
download_pdf e AnthropicContractExtractor.extract são substituídos;
extract_text (pypdf), detect_price_anomalies, homologação, RLS e o
motor de risco de glosa rodam 100% reais), confere o alerta de
anomalia de preço determinístico contra um contrato anterior de
verdade no Postgres, simula a CORREÇÃO HUMANA na Tela de Conferência
(o item com anomalia é corrigido antes de homologar — não o valor que
a IA "leu" errado), e por fim fatura de verdade (upload de CSV) contra
a tabela homologada para confirmar que financial_hole/
denial_at_risk_value batem exatamente com o valor CORRIGIDO pelo
humano, não o extraído originalmente pela IA — prova de que é a
homologação, não a extração, que "existe" para o motor de glosa (ver
DECISÃO em contract_intake_service.py).
"""
import io
import uuid
from datetime import date, timedelta

import pytest
from reportlab.pdfgen import canvas
from sqlalchemy import text

from app.services import contract_intake_service as intake_module
from app.services import contract_storage_client as contract_storage_module
from app.services import ingestion_storage_client as ingestion_storage_module
from app.services.contract_extraction_service import ExtractedItem, ExtractionResult


@pytest.fixture(autouse=True)
def _fake_contract_storage(monkeypatch):
    """Mesma técnica de test_ingestion_upload.py: mocka SÓ a chamada de
    rede real ao S3 (bucket separado do de ingestão — ver DECISÃO em
    contract_storage_client.py). Um dict em memória faz o papel do
    bucket: create_draft grava, run_extraction lê de volta — prova que
    o PDF que realmente sobe é o mesmo que é baixado e lido pelo
    extrator de texto de verdade (pypdf) depois, não um texto fixo de
    teste."""
    monkeypatch.setattr(contract_storage_module.settings, "AWS_S3_CONTRACTS_BUCKET", "bucket-teste-contratos")
    store: dict[str, bytes] = {}

    async def _fake_upload_pdf(self, *, key: str, pdf_bytes: bytes) -> None:
        store[key] = pdf_bytes

    async def _fake_download_pdf(self, *, key: str) -> bytes:
        return store[key]

    monkeypatch.setattr(contract_storage_module.ContractStorageClient, "upload_pdf", _fake_upload_pdf)
    monkeypatch.setattr(contract_storage_module.ContractStorageClient, "download_pdf", _fake_download_pdf)
    yield


@pytest.fixture(autouse=True)
def _fake_ingestion_bucket(monkeypatch):
    """Mesmo mock de test_ingestion_reconciliation.py — usado na segunda
    metade deste teste, quando faturamos de verdade contra o contrato
    recém-homologado."""
    monkeypatch.setattr(ingestion_storage_module.settings, "AWS_S3_INGEST_BUCKET", "bucket-teste-ingestao-contratos")

    async def _fake_upload_bytes(self, *, key: str, raw_bytes: bytes) -> str | None:
        return None

    monkeypatch.setattr(ingestion_storage_module.IngestionStorageClient, "upload_bytes", _fake_upload_bytes)
    yield


class _FakeAnthropicExtractor:
    """Substitui AnthropicContractExtractor SÓ para não precisar de
    ANTHROPIC_API_KEY/rede real neste sandbox — devolve uma extração
    determinística. Tudo que vem DEPOIS disso no fluxo real
    (detect_price_anomalies, persistência, homologação, motor de
    glosa) roda sem nenhum mock. `captured_pdf_text` guarda o texto que
    de fato chegou aqui, para provar que veio do PDF real (via
    contract_pdf_text.extract_text), não de um valor fixo."""

    captured_pdf_text: str | None = None

    def __init__(self):
        pass

    async def extract(self, pdf_text: str) -> ExtractionResult:
        _FakeAnthropicExtractor.captured_pdf_text = pdf_text
        return ExtractionResult(
            items=[
                ExtractedItem(tuss_code="10101012", procedure_name="Consulta em consultório", agreed_price=180.00),
                ExtractedItem(tuss_code="20103019", procedure_name="Fisioterapia", agreed_price=95.00),
                # Este código já tem um contrato homologado ANTERIOR a
                # R$50,00 (ver _create_previous_homologated_contract) — a
                # IA "lê" R$150,00 na tabela nova, 3x o valor anterior.
                # Isso deve disparar o aviso de anomalia via
                # detect_price_anomalies (aritmética real, não mockada).
                ExtractedItem(tuss_code="30111000", procedure_name="Procedimento X", agreed_price=150.00),
            ],
            warnings=[],
        )


@pytest.fixture(autouse=True)
def _fake_extractor(monkeypatch):
    monkeypatch.setattr(intake_module, "AnthropicContractExtractor", _FakeAnthropicExtractor)
    yield


def _build_contract_pdf_bytes() -> bytes:
    """PDF DE VERDADE com texto extraível de verdade via pypdf — não um
    fixture hardcoded de bytes, para provar que o caminho real
    (download do storage -> contract_pdf_text.extract_text) funciona
    fim a fim, e não só a parte que fica depois disso."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, "Tabela de Precos - Convenio Unimed Nacional - Vigencia 2026")
    c.drawString(100, 730, "10101012 - Consulta em consultorio - R$ 180,00")
    c.drawString(100, 710, "20103019 - Fisioterapia - R$ 95,00")
    c.drawString(100, 690, "30111000 - Procedimento X - R$ 150,00")
    c.save()
    return buf.getvalue()


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Unimed Nacional", normalized_key="unimed_nacional") -> str:
    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :n, :k)"),
            {"id": plan_id, "t": tenant_id, "n": display_name, "k": normalized_key},
        )
    return plan_id


async def _create_previous_homologated_contract(admin_engine, tenant_id, plan_id) -> None:
    """Contrato ANTERIOR já homologado, só com o código que vai disparar
    a anomalia de preço na extração nova (ver _FakeAnthropicExtractor) —
    detect_price_anomalies compara contra ESTE contrato via
    ContractItemRepository.list_items_for_previous_homologated_contract.
    Vigência encerrada em 2025 de propósito: essa consulta não filtra
    por `as_of`, então continua valendo como "tabela anterior" mesmo
    fora de vigência hoje — só find_agreed_price (motor de glosa) olha
    vigência, e ali é o contrato NOVO (homologado no passo 3) que deve
    valer para o faturamento do passo 4."""
    contract_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.contracts (id, tenant_id, insurance_plan_id, valid_from, valid_until, status) "
                "VALUES (:id, :t, :plan, '2025-01-01', '2025-12-31', 'homologado')"
            ),
            {"id": contract_id, "t": tenant_id, "plan": plan_id},
        )
        await conn.execute(
            text(
                "INSERT INTO core.contract_items (tenant_id, contract_id, tuss_code, agreed_price) "
                "VALUES (:t, :contract, '30111000', 50.00)"
            ),
            {"t": tenant_id, "contract": contract_id},
        )


def _window() -> tuple[str, str]:
    today = date.today()
    return (today - timedelta(days=1)).isoformat(), (today + timedelta(days=1)).isoformat()


async def test_pdf_upload_extraction_and_homologation_reconcile_against_real_billing(
    client, auth_headers_a, admin_engine, tenant_a
):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_previous_homologated_contract(admin_engine, tenant_a, plan_id)

    # --- Passo 1: upload do PDF (cria o rascunho, sem itens ainda) ---
    files = {"file": ("tabela_unimed_2026.pdf", io.BytesIO(_build_contract_pdf_bytes()), "application/pdf")}
    draft_resp = await client.post(
        "/api/v1/contracts/upload",
        data={"insurance_plan_id": plan_id, "valid_from": date.today().isoformat()},
        files=files,
        headers=auth_headers_a,
    )
    assert draft_resp.status_code == 201, draft_resp.text
    draft = draft_resp.json()
    assert draft["status"] == "rascunho"
    assert draft["items"] == []
    assert draft["pdf_s3_key"] is not None
    contract_id = draft["id"]

    # --- Passo 2: extração (IA falsa + detecção de anomalia REAL) ---
    extract_resp = await client.post(f"/api/v1/contracts/{contract_id}/extract", headers=auth_headers_a)
    assert extract_resp.status_code == 200, extract_resp.text
    preview = extract_resp.json()
    assert preview["status"] == "em_revisao"

    # Prova de que o texto que chegou ao extrator veio de verdade do PDF
    # real (via pypdf), não de um texto fixo de teste.
    assert "Tabela de Precos" in _FakeAnthropicExtractor.captured_pdf_text
    assert "180,00" in _FakeAnthropicExtractor.captured_pdf_text

    by_code = {i["tuss_code"]: i for i in preview["items"]}
    assert by_code["10101012"]["agreed_price"] == pytest.approx(180.00)
    assert by_code["10101012"]["warning"] is None
    assert by_code["20103019"]["warning"] is None
    # Anomalia: 150,00 é 3x o valor anterior (50,00) do mesmo código —
    # cálculo determinístico de detect_price_anomalies, sem IA nenhuma.
    assert "3,0x acima" in by_code["30111000"]["warning"]
    assert any("preço muito acima da tabela anterior" in w for w in preview["warnings"])

    # --- Passo 3: homologação — humano CORRIGE o item com anomalia ---
    # (decide que R$150,00 foi leitura errada da IA e digita o valor
    # correto, R$55,00, antes de confirmar — o Human-in-the-Loop que
    # justifica a IA nunca persistir direto, ver DECISÃO no service).
    homologate_resp = await client.post(
        f"/api/v1/contracts/{contract_id}/homologate",
        json={
            "items": [
                {"tuss_code": "10101012", "procedure_name": "Consulta em consultório", "agreed_price": 180.00},
                {"tuss_code": "20103019", "procedure_name": "Fisioterapia", "agreed_price": 95.00},
                {"tuss_code": "30111000", "procedure_name": "Procedimento X", "agreed_price": 55.00},
            ]
        },
        headers=auth_headers_a,
    )
    assert homologate_resp.status_code == 200, homologate_resp.text
    homologated = homologate_resp.json()
    assert homologated["status"] == "homologado"
    homologated_by_code = {i["tuss_code"]: i["agreed_price"] for i in homologated["items"]}
    assert homologated_by_code == {"10101012": 180.00, "20103019": 95.00, "30111000": 55.00}

    # --- Passo 4: fatura de verdade contra a tabela recém-homologada ---
    header = "cpf_paciente;nome_paciente;convenio;codigo_procedimento;cid;valor_cobrado;data_atendimento"
    service_date = (date.today() - timedelta(days=2)).strftime("%d/%m/%Y")
    rows = [
        f"11111111111;Paciente Exato;Unimed Nacional;10101012;J06;180,00;{service_date}",
        f"22222222222;Paciente Abaixo;Unimed Nacional;20103019;J06;90,00;{service_date}",
        # Cobrou 70,00 contra o valor CORRIGIDO (55,00) — se o sistema
        # ainda estivesse usando os R$150,00 que a IA leu originalmente,
        # isso não dispararia glosa nenhuma (70 < 150). É a prova de que
        # é o valor HOMOLOGADO pelo humano que vale para o motor de
        # risco, não o extraído.
        f"33333333333;Paciente Acima;Unimed Nacional;30111000;J06;70,00;{service_date}",
    ]
    csv_bytes = ("\r\n".join([header] + rows) + "\r\n").encode("utf-8-sig")
    upload_resp = await client.post(
        "/api/v1/ingestion/upload",
        files={"file": ("faturamento_unimed.csv", io.BytesIO(csv_bytes), "text/csv")},
        headers=auth_headers_a,
    )
    assert upload_resp.status_code == 201, upload_resp.text
    assert upload_resp.json()["row_count"] == 3
    assert upload_resp.json()["error_row_count"] == 0

    date_from, date_to = _window()
    summary = (
        await client.get(f"/api/v1/analytics/executive-summary?date_from={date_from}&date_to={date_to}", headers=auth_headers_a)
    ).json()

    # Total faturado = 180 + 90 + 70 = 340,00
    assert summary["total_billed"]["value"] == pytest.approx(340.00)
    # Buraco financeiro = só "Paciente Abaixo" (95 acordado - 90 cobrado)
    # = 5,00. "Paciente Acima" (70 vs 55 homologado) é cobrança ACIMA do
    # contrato — glosa em potencial, não buraco de faturamento (ver
    # DECISÃO em analytics_repository.financial_hole_total).
    assert summary["financial_hole"]["value"] == pytest.approx(5.00)
    # Só "Paciente Acima" é risco ALTO (70 vs 55, acima da tolerância) —
    # "Paciente Abaixo" é risco MÉDIO (vazamento de receita), não conta
    # em high_risk_pending_count.
    assert summary["high_risk_pending_count"] == 1
    # Valor em risco médio+alto = 90 (Paciente Abaixo, medium) + 70
    # (Paciente Acima, high) = 160,00.
    assert summary["denial_at_risk_value"] == pytest.approx(160.00)

    plan_ranking = (
        await client.get(f"/api/v1/analytics/plan-loss-ranking?date_from={date_from}&date_to={date_to}", headers=auth_headers_a)
    ).json()
    by_plan = {p["plan_name"]: p for p in plan_ranking["plans"]}
    assert by_plan["Unimed Nacional"]["financial_hole"] == pytest.approx(5.00)
