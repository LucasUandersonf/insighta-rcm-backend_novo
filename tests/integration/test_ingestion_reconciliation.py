"""
tests/integration/test_ingestion_reconciliation.py

Teste de RECONCILIAÇÃO fim a fim do caminho síncrono de ingestão (POST
/ingestion/upload): sobe um arquivo com uma mistura DELIBERADA de casos
reais (cobrança exata, abaixo do contratado, acima do contratado, CID
ausente, convênio não reconhecido repetido, formato de moeda BR com
milhar+decimal, uma linha estruturalmente inválida) e confere que os
NÚMEROS dos Dashboards de Decisão batem exatamente com o que se espera
calculando à mão a partir do próprio arquivo — não só que o upload
"funcionou".

Mesma técnica de mock de fronteira de rede que test_ingestion_upload.py
(só a chamada real ao S3 é substituída; parsing, normalização, motor de
risco de glosa e Postgres são 100% reais) — ver a docstring completa lá
para a justificativa.
"""
import io
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.services import ingestion_storage_client as storage_module


@pytest.fixture(autouse=True)
def _fake_ingestion_bucket(monkeypatch):
    monkeypatch.setattr(storage_module.settings, "AWS_S3_INGEST_BUCKET", "bucket-teste-reconciliacao")

    async def _fake_upload_bytes(self, *, key: str, raw_bytes: bytes) -> str | None:
        return None

    monkeypatch.setattr(storage_module.IngestionStorageClient, "upload_bytes", _fake_upload_bytes)
    yield


async def _create_insurance_plan(admin_engine, tenant_id, display_name, normalized_key) -> str:
    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :n, :k)"),
            {"id": plan_id, "t": tenant_id, "n": display_name, "k": normalized_key},
        )
    return plan_id


async def _create_contract(admin_engine, tenant_id, plan_id, items: dict[str, float]):
    contract_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.contracts (id, tenant_id, insurance_plan_id, valid_from, status) "
                "VALUES (:id, :t, :plan, '2026-01-01', 'homologado')"
            ),
            {"id": contract_id, "t": tenant_id, "plan": plan_id},
        )
        for code, price in items.items():
            await conn.execute(
                text(
                    "INSERT INTO core.contract_items (tenant_id, contract_id, tuss_code, agreed_price) "
                    "VALUES (:t, :contract, :code, :price)"
                ),
                {"t": tenant_id, "contract": contract_id, "code": code, "price": price},
            )


# CID em branco na linha 3 é DE PROPÓSITO (dispara missing_cid); a linha
# 9 tem data em formato ISO em vez de dd/mm/aaaa — inválida
# estruturalmente para o parser de CSV, de propósito. data_atendimento
# em si não importa para a reconciliação (billing_summary/financial_hole/
# etc. filtram por Billing.created_at — "agora", não a data clínica do
# atendimento importado), mas usar uma data plausível de qualquer forma
# evita um atendimento "no futuro" esquisito nos dados.
_SERVICE_DATE = (date.today() - timedelta(days=5)).strftime("%d/%m/%Y")
_BAD_SERVICE_DATE = (date.today() - timedelta(days=5)).isoformat()  # ISO, não dd/mm/aaaa — estruturalmente inválida

_CSV_ROWS = [
    # nome, convenio, procedimento, cid, valor
    ("Ana Costa", "Unimed Nacional", "10101012", "J06", "180,00"),
    ("Bruno Lima", "Unimed Nacional", "10101012", "J06", "140,00"),
    ("Carla Dias", "Unimed Nacional", "10101012", "", "180,00"),
    ("Diego Alves", "Unimed Nacional", "10101012", "J06", "250,00"),
    ("Elaine Souza", "UNIMED NAC.", "20103019", "M54.5", "95,00"),
    ("Fábio Melo", "UNIMED NAC.", "10101012", "J06", "180,00"),
    ("Giovana Reis", "SulAmérica", "10101012", "J06", "200,00"),
    ("Hugo Prado", "Unimed Nacional", "10101012", "J06", "1.500,00"),
    ("Igor Falha", "Unimed Nacional", "10101012", "J06", "180,00"),
]


def _build_csv() -> bytes:
    header = "cpf_paciente;nome_paciente;convenio;codigo_procedimento;cid;valor_cobrado;data_atendimento"
    lines = [header]
    for i, (nome, convenio, proc, cid, valor) in enumerate(_CSV_ROWS):
        cpf = f"{i + 1:011d}"
        data = _BAD_SERVICE_DATE if nome == "Igor Falha" else _SERVICE_DATE
        lines.append(f"{cpf};{nome};{convenio};{proc};{cid};{valor};{data}")
    return ("\r\n".join(lines) + "\r\n").encode("utf-8-sig")


def _window() -> tuple[str, str]:
    # billing_summary/financial_hole_total/etc. filtram por
    # Billing.created_at ("agora", não a data clínica do atendimento
    # importado) — uma janela de hoje ± 1 dia sempre cobre um billing
    # recém-criado neste mesmo teste.
    today = date.today()
    return (today - timedelta(days=1)).isoformat(), (today + timedelta(days=1)).isoformat()


@pytest.fixture
async def _plans_and_contracts(admin_engine, tenant_a):
    unimed_id = await _create_insurance_plan(admin_engine, tenant_a, "Unimed Nacional", "unimed_nacional")
    await _create_contract(admin_engine, tenant_a, unimed_id, {"10101012": 180.00, "20103019": 95.00})
    sulamerica_id = await _create_insurance_plan(admin_engine, tenant_a, "SulAmérica", "sulamerica")
    await _create_contract(admin_engine, tenant_a, sulamerica_id, {"10101012": 200.00})
    return {"unimed": unimed_id, "sulamerica": sulamerica_id}


async def test_upload_reconciles_exactly_against_hand_computed_totals(client, auth_headers_a, admin_engine, tenant_a, _plans_and_contracts):
    files = {"file": ("faturamento_agosto.csv", io.BytesIO(_build_csv()), "text/csv")}
    upload_resp = await client.post("/api/v1/ingestion/upload", files=files, headers=auth_headers_a)
    assert upload_resp.status_code == 201, upload_resp.text
    upload_body = upload_resp.json()

    # 9 linhas no arquivo; 6 normalizadas (linhas 1,2,3,4,7,8); 2
    # rejeitadas por convênio desconhecido (linhas 5,6, mesmo raw_value);
    # 1 estruturalmente inválida (linha 9, data em formato errado).
    assert upload_body["row_count"] == 9
    # BUG CORRIGIDO: error_row_count contava a linha 9 (estruturalmente
    # inválida) duas vezes — ver DECISÃO em
    # NormalizationService.normalize_rows. O total correto é 3 (linhas
    # 5, 6 e 9), não 4.
    assert upload_body["error_row_count"] == 3

    date_from, date_to = _window()
    summary = (
        await client.get(f"/api/v1/analytics/executive-summary?date_from={date_from}&date_to={date_to}", headers=auth_headers_a)
    ).json()

    # Total faturado = soma do charged_value das 6 linhas normalizadas:
    # 180 + 140 + 180 + 250 + 200 + 1500 = 2450.00 — prova, de quebra,
    # que o parser de moeda BR interpretou "1.500,00" como R$1.500,00
    # (milhar + decimal), não R$1,50 nem R$150000.
    assert summary["total_billed"]["value"] == pytest.approx(2450.00)

    # Buraco financeiro = só a linha 2 (Unimed, cobrou 140 de um
    # contratado 180) = 40.00. As demais são exatas ou acima (não é buraco).
    assert summary["financial_hole"]["value"] == pytest.approx(40.00)

    # Risco de glosa: linha 3 (CID ausente) + linha 4 (acima do
    # contratado, 250 vs 180) + linha 8 (1500 vs 180) = 3 faturamentos
    # held_for_review (nível "high").
    assert summary["high_risk_pending_count"] == 3

    # Valor em risco médio/alto: linha 2 (140, medium) + linha 3 (180,
    # high) + linha 4 (250, high) + linha 8 (1500, high) = 2070.00.
    assert summary["denial_at_risk_value"] == pytest.approx(2070.00)

    plan_ranking = (
        await client.get(f"/api/v1/analytics/plan-loss-ranking?date_from={date_from}&date_to={date_to}", headers=auth_headers_a)
    ).json()
    by_plan = {p["plan_name"]: p for p in plan_ranking["plans"]}
    assert "Unimed Nacional" in by_plan
    assert by_plan["Unimed Nacional"]["financial_hole"] == pytest.approx(40.00)
    # SulAmérica (linha 7, 200 == contratado) não deveria aparecer no
    # ranking de perda — não teve nenhuma perda no período.
    assert "SulAmérica" not in by_plan

    # --- Linhas rejeitadas: convênio não reconhecido ---
    rejected = (await client.get("/api/v1/ingestion/rejected", headers=auth_headers_a)).json()
    unknown_plan_rows = [r for r in rejected if r["reason"] == "unknown_insurance_plan"]
    assert len(unknown_plan_rows) == 2
    assert {r["payload"]["patient_name"] for r in unknown_plan_rows} == {"Elaine Souza", "Fábio Melo"}

    # Resolve UMA das duas linhas rejeitadas (Elaine) mapeando "UNIMED
    # NAC." para o plano certo — a outra (Fábio, mesmo raw_value) deve
    # ser promovida automaticamente junto (ver DECISÃO em
    # NormalizationService.resolve_unknown_insurance_plan).
    target_row_id = next(r["id"] for r in unknown_plan_rows if r["payload"]["patient_name"] == "Elaine Souza")
    resolve_resp = await client.post(
        f"/api/v1/ingestion/rejected/{target_row_id}/resolve-insurance-plan",
        json={"insurance_plan_id": _plans_and_contracts["unimed"]},
        headers=auth_headers_a,
    )
    assert resolve_resp.status_code == 200
    resolve_body = resolve_resp.json()
    assert resolve_body["resolved"] is True
    assert resolve_body["additionally_resolved_count"] == 1

    # Depois de resolvido: as duas linhas (Elaine 95,00 exato no
    # contrato de Fisioterapia + Fábio 180,00 exato em Consulta) entram
    # no total faturado — 2450 + 95 + 180 = 2725.00. Nenhuma delas gera
    # buraco/risco novo (ambas batem exatamente com a tabela).
    summary_after = (
        await client.get(f"/api/v1/analytics/executive-summary?date_from={date_from}&date_to={date_to}", headers=auth_headers_a)
    ).json()
    assert summary_after["total_billed"]["value"] == pytest.approx(2725.00)
    assert summary_after["financial_hole"]["value"] == pytest.approx(40.00)  # inalterado
    assert summary_after["high_risk_pending_count"] == 3  # inalterado

    remaining_rejected = (await client.get("/api/v1/ingestion/rejected", headers=auth_headers_a)).json()
    remaining_unknown = [r for r in remaining_rejected if r["reason"] == "unknown_insurance_plan"]
    assert remaining_unknown == []


async def test_xml_and_json_uploads_reconcile_identically_to_csv(client, auth_headers_a, admin_engine, tenant_a, _plans_and_contracts):
    """Os 3 formatos que a Central de Upload aceita (CSV/XML/JSON) devem
    produzir o MESMO resultado de negócio para o mesmo atendimento —
    prova de paridade entre os três parsers (app/worker/parsers/), não
    só do CSV já coberto acima."""
    service_date_iso = (date.today() - timedelta(days=3)).isoformat()

    xml_bytes = f"""<?xml version="1.0" encoding="UTF-8"?>
<lote>
  <atendimento>
    <cpfPaciente>99988877766</cpfPaciente>
    <nomePaciente>Julia Ramos</nomePaciente>
    <convenio>Unimed Nacional</convenio>
    <codigoProcedimento>10101012</codigoProcedimento>
    <cid>J06</cid>
    <valorCobrado>180,00</valorCobrado>
    <dataAtendimento>{service_date_iso}</dataAtendimento>
  </atendimento>
</lote>""".encode("utf-8")

    json_bytes = (
        '[{"cpf_paciente": "99988877765", "nome_paciente": "Karla Nunes", '
        '"convenio": "Unimed Nacional", "codigo_procedimento": "10101012", "cid": "J06", '
        f'"valor_cobrado": 180.00, "data_atendimento": "{service_date_iso}"}}]'
    ).encode("utf-8")

    xml_resp = await client.post(
        "/api/v1/ingestion/upload", files={"file": ("lote.xml", io.BytesIO(xml_bytes), "application/xml")}, headers=auth_headers_a
    )
    assert xml_resp.status_code == 201, xml_resp.text
    assert xml_resp.json()["row_count"] == 1
    assert xml_resp.json()["error_row_count"] == 0

    json_resp = await client.post(
        "/api/v1/ingestion/upload", files={"file": ("lote.json", io.BytesIO(json_bytes), "application/json")}, headers=auth_headers_a
    )
    assert json_resp.status_code == 201, json_resp.text
    assert json_resp.json()["row_count"] == 1
    assert json_resp.json()["error_row_count"] == 0

    date_from, date_to = _window()
    summary = (
        await client.get(f"/api/v1/analytics/executive-summary?date_from={date_from}&date_to={date_to}", headers=auth_headers_a)
    ).json()
    # Duas consultas de R$180,00 cada, exatamente no valor contratado —
    # nenhum buraco financeiro, nenhum risco.
    assert summary["total_billed"]["value"] == pytest.approx(360.00)
    assert summary["financial_hole"]["value"] == pytest.approx(0.0)
