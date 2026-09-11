"""
tests/integration/test_ingestion_glosa.py

Cobre o Template de Integração "Glosa" (demonstrativo de pagamento — ver
docstring de RawDenialRow em app/worker/schemas.py) via
POST /ingestion/upload?data_type=glosa — mesma técnica de mock (só a
fronteira de rede do S3) de test_ingestion_agenda.py.

Diferença central testada aqui: uma linha de Glosa NUNCA cria
Appointment/Patient/Billing novo — ela CASA com um Billing que o
template de Faturamento já criou (via convênio + Guia.numero) e:
  1. faz o settle (received_value/status/settled_at nesse Billing), e
  2. quando pago < cobrado, cria a Glosa REAL correspondente em
     core.glosas (fato confirmado — diferente de Billing.denial_reasons,
     que são os motivos PREVISTOS pelo motor de risco).
"""
import io
import json
import uuid

import pytest
from sqlalchemy import text

from app.services import ingestion_storage_client as storage_module


@pytest.fixture(autouse=True)
def _fake_ingestion_bucket(monkeypatch):
    """Mesma técnica de test_ingestion_agenda.py — ver docstring de lá."""
    monkeypatch.setattr(storage_module.settings, "AWS_S3_INGEST_BUCKET", "bucket-teste-ingestao-glosa")

    async def _fake_upload_bytes(self, *, key: str, raw_bytes: bytes) -> str | None:
        return None

    monkeypatch.setattr(storage_module.IngestionStorageClient, "upload_bytes", _fake_upload_bytes)
    yield


_FATURAMENTO_HEADER = (
    "cpf_paciente;nome_paciente;convenio;codigo_procedimento;cid;valor_cobrado;data_atendimento;"
    "guia_tipo;guia_numero;guia_senha"
)
# Variante com numero_carteirinha, usada pelos testes de chave de
# conciliação alternativa (ver DECISÃO em RawDenialRow) — sem guia_tipo/
# guia_numero/guia_senha, para simular exatamente o cenário que o achado
# do Dicionário de Dados descreve: cobrança registrada só com carteirinha.
_FATURAMENTO_HEADER_SEM_GUIA = (
    "cpf_paciente;nome_paciente;convenio;codigo_procedimento;cid;valor_cobrado;data_atendimento;numero_carteirinha"
)
_GLOSA_HEADER = "convenio;guia_numero;codigo_procedimento;valor_pago;data_pagamento;codigo_motivo_glosa;descricao_motivo_glosa"
_GLOSA_HEADER_CARTEIRINHA = (
    "convenio;numero_carteirinha;codigo_procedimento;valor_pago;data_pagamento;codigo_motivo_glosa;descricao_motivo_glosa"
)
# Variante com cpf_beneficiario, usada pelos testes de confirmação
# cruzada de identidade (Achado 6 da Auditoria de Templates e Insights) —
# ver DECISÃO em RawDenialRow.patient_cpf.
_GLOSA_HEADER_CARTEIRINHA_COM_CPF = (
    "convenio;numero_carteirinha;cpf_beneficiario;codigo_procedimento;valor_pago;data_pagamento;"
    "codigo_motivo_glosa;descricao_motivo_glosa"
)


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Unimed Nacional", normalized_key="unimed_nacional") -> str:
    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :n, :k)"),
            {"id": plan_id, "t": tenant_id, "n": display_name, "k": normalized_key},
        )
    return plan_id


async def _fetch_one(admin_engine, sql: str, **params) -> dict | None:
    async with admin_engine.begin() as conn:
        result = await conn.execute(text(sql), params)
        row = result.mappings().first()
        return dict(row) if row is not None else None


async def _fetch_all(admin_engine, sql: str, **params) -> list[dict]:
    async with admin_engine.begin() as conn:
        result = await conn.execute(text(sql), params)
        return [dict(r) for r in result.mappings().all()]


async def _upload_faturamento(client, auth_headers, *rows: str, filename: str = "faturamento.csv"):
    body = (_FATURAMENTO_HEADER + "\r\n" + "\r\n".join(rows) + "\r\n").encode("utf-8-sig")
    files = {"file": (filename, io.BytesIO(body), "text/csv")}
    return await client.post("/api/v1/ingestion/upload", files=files, data={"data_type": "faturamento"}, headers=auth_headers)


async def _upload_faturamento_sem_guia(client, auth_headers, *rows: str, filename: str = "faturamento.csv"):
    body = (_FATURAMENTO_HEADER_SEM_GUIA + "\r\n" + "\r\n".join(rows) + "\r\n").encode("utf-8-sig")
    files = {"file": (filename, io.BytesIO(body), "text/csv")}
    return await client.post("/api/v1/ingestion/upload", files=files, data={"data_type": "faturamento"}, headers=auth_headers)


async def _upload_glosa(client, auth_headers, *rows: str, filename: str = "glosa.csv"):
    body = (_GLOSA_HEADER + "\r\n" + "\r\n".join(rows) + "\r\n").encode("utf-8-sig")
    files = {"file": (filename, io.BytesIO(body), "text/csv")}
    return await client.post("/api/v1/ingestion/upload", files=files, data={"data_type": "glosa"}, headers=auth_headers)


async def _upload_glosa_por_carteirinha(client, auth_headers, *rows: str, filename: str = "glosa.csv"):
    body = (_GLOSA_HEADER_CARTEIRINHA + "\r\n" + "\r\n".join(rows) + "\r\n").encode("utf-8-sig")
    files = {"file": (filename, io.BytesIO(body), "text/csv")}
    return await client.post("/api/v1/ingestion/upload", files=files, data={"data_type": "glosa"}, headers=auth_headers)


async def _upload_glosa_por_carteirinha_com_cpf(client, auth_headers, *rows: str, filename: str = "glosa.csv"):
    body = (_GLOSA_HEADER_CARTEIRINHA_COM_CPF + "\r\n" + "\r\n".join(rows) + "\r\n").encode("utf-8-sig")
    files = {"file": (filename, io.BytesIO(body), "text/csv")}
    return await client.post("/api/v1/ingestion/upload", files=files, data={"data_type": "glosa"}, headers=auth_headers)


async def test_glosa_partial_payment_settles_billing_and_creates_glosa(client, auth_headers_a, admin_engine, tenant_a):
    await _create_insurance_plan(admin_engine, tenant_a)
    fat_row = "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;500,00;20/08/2026;sadt;G-001;"
    fat = await _upload_faturamento(client, auth_headers_a, fat_row)
    assert fat.status_code == 201, fat.text
    assert fat.json()["error_row_count"] == 0

    glosa_row = "Unimed Nacional;G-001;10101012;350,00;25/08/2026;;"
    resp = await _upload_glosa(client, auth_headers_a, glosa_row)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["data_type"] == "glosa"
    assert body["row_count"] == 1
    assert body["error_row_count"] == 0

    billing = await _fetch_one(admin_engine, "SELECT * FROM core.billing WHERE tenant_id = :t", t=tenant_a)
    assert float(billing["received_value"]) == 350.0
    assert billing["status"] == "paid"
    assert billing["settled_at"] is not None

    glosa = await _fetch_one(admin_engine, "SELECT * FROM core.glosas WHERE tenant_id = :t", t=tenant_a)
    assert glosa is not None
    assert glosa["billing_id"] == billing["id"]
    assert float(glosa["valor_glosado"]) == 150.0  # 500 - 350


async def test_glosa_full_payment_settles_without_creating_glosa(client, auth_headers_a, admin_engine, tenant_a):
    """pago == cobrado -> nenhuma glosa de fato aconteceu, core.glosas
    continua vazia (nunca cria um registro de valor zero/negativo)."""
    await _create_insurance_plan(admin_engine, tenant_a)
    fat_row = "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;500,00;20/08/2026;sadt;G-002;"
    await _upload_faturamento(client, auth_headers_a, fat_row)

    glosa_row = "Unimed Nacional;G-002;10101012;500,00;25/08/2026;;"
    resp = await _upload_glosa(client, auth_headers_a, glosa_row)
    assert resp.status_code == 201, resp.text
    assert resp.json()["error_row_count"] == 0

    billing = await _fetch_one(admin_engine, "SELECT * FROM core.billing WHERE tenant_id = :t", t=tenant_a)
    assert float(billing["received_value"]) == 500.0
    assert billing["status"] == "paid"

    glosas = await _fetch_all(admin_engine, "SELECT * FROM core.glosas WHERE tenant_id = :t", t=tenant_a)
    assert glosas == []


async def test_glosa_full_denial_sets_denied_status_and_full_glosa_value(client, auth_headers_a, admin_engine, tenant_a):
    await _create_insurance_plan(admin_engine, tenant_a)
    fat_row = "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;500,00;20/08/2026;sadt;G-003;"
    await _upload_faturamento(client, auth_headers_a, fat_row)

    glosa_row = "Unimed Nacional;G-003;10101012;0,00;25/08/2026;CO-45;Procedimento nao autorizado"
    resp = await _upload_glosa(client, auth_headers_a, glosa_row)
    assert resp.status_code == 201, resp.text
    assert resp.json()["error_row_count"] == 0

    billing = await _fetch_one(admin_engine, "SELECT * FROM core.billing WHERE tenant_id = :t", t=tenant_a)
    assert float(billing["received_value"]) == 0.0
    assert billing["status"] == "denied"

    glosa = await _fetch_one(admin_engine, "SELECT * FROM core.glosas WHERE tenant_id = :t", t=tenant_a)
    assert float(glosa["valor_glosado"]) == 500.0
    assert glosa["codigo_motivo"] == "CO-45"
    assert glosa["descricao_motivo"] == "Procedimento nao autorizado"


async def test_glosa_row_with_unknown_guia_is_rejected(client, auth_headers_a, admin_engine, tenant_a):
    await _create_insurance_plan(admin_engine, tenant_a)
    glosa_row = "Unimed Nacional;GUIA-INEXISTENTE;;300,00;25/08/2026;;"
    resp = await _upload_glosa(client, auth_headers_a, glosa_row)
    assert resp.status_code == 201, resp.text
    assert resp.json()["error_row_count"] == 1

    glosas = await _fetch_all(admin_engine, "SELECT * FROM core.glosas WHERE tenant_id = :t", t=tenant_a)
    assert glosas == []


async def test_glosa_ambiguous_guia_without_procedure_code_is_rejected(client, auth_headers_a, admin_engine, tenant_a):
    """Guia com DUAS linhas de billing (SADT com dois procedimentos) —
    sem procedure_code no demonstrativo, não há como saber qual das duas
    está sendo liquidada. Rejeita em vez de adivinhar."""
    await _create_insurance_plan(admin_engine, tenant_a)
    rows = [
        "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;500,00;20/08/2026;sadt;G-004;",
        "12345678900;Paciente Teste;Unimed Nacional;20102030;J06;200,00;20/08/2026;sadt;G-004;",
    ]
    fat = await _upload_faturamento(client, auth_headers_a, *rows)
    assert fat.status_code == 201, fat.text
    assert fat.json()["error_row_count"] == 0

    glosa_row = "Unimed Nacional;G-004;;700,00;25/08/2026;;"
    resp = await _upload_glosa(client, auth_headers_a, glosa_row)
    assert resp.status_code == 201, resp.text
    assert resp.json()["error_row_count"] == 1

    billings = await _fetch_all(
        admin_engine, "SELECT * FROM core.billing WHERE tenant_id = :t AND received_value IS NOT NULL", t=tenant_a
    )
    assert billings == []


async def test_glosa_disambiguates_by_procedure_code(client, auth_headers_a, admin_engine, tenant_a):
    """Mesmo cenário acima, mas o demonstrativo traz codigo_procedimento
    — liquida SÓ a linha certa, a outra fica intocada."""
    await _create_insurance_plan(admin_engine, tenant_a)
    rows = [
        "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;500,00;20/08/2026;sadt;G-005;",
        "12345678900;Paciente Teste;Unimed Nacional;20102030;J06;200,00;20/08/2026;sadt;G-005;",
    ]
    await _upload_faturamento(client, auth_headers_a, *rows)

    glosa_row = "Unimed Nacional;G-005;10101012;500,00;25/08/2026;;"
    resp = await _upload_glosa(client, auth_headers_a, glosa_row)
    assert resp.status_code == 201, resp.text
    assert resp.json()["error_row_count"] == 0

    settled = await _fetch_all(
        admin_engine, "SELECT * FROM core.billing WHERE tenant_id = :t AND received_value IS NOT NULL", t=tenant_a
    )
    assert len(settled) == 1
    assert float(settled[0]["charged_value"]) == 500.0

    untouched = await _fetch_all(
        admin_engine, "SELECT * FROM core.billing WHERE tenant_id = :t AND received_value IS NULL", t=tenant_a
    )
    assert len(untouched) == 1
    assert float(untouched[0]["charged_value"]) == 200.0


async def test_glosa_row_with_unknown_insurance_plan_is_rejected(client, auth_headers_a, admin_engine, tenant_a):
    glosa_row = "Convenio Desconhecido;G-006;;300,00;25/08/2026;;"
    resp = await _upload_glosa(client, auth_headers_a, glosa_row)
    assert resp.status_code == 201, resp.text
    assert resp.json()["error_row_count"] == 1


async def test_glosa_upload_via_xml(client, auth_headers_a, admin_engine, tenant_a):
    await _create_insurance_plan(admin_engine, tenant_a)
    fat_row = "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;500,00;20/08/2026;sadt;G-XML-1;"
    await _upload_faturamento(client, auth_headers_a, fat_row)

    xml_bytes = (
        b"<pagamentos><pagamento>"
        b"<convenio>Unimed Nacional</convenio>"
        b"<guiaNumero>G-XML-1</guiaNumero>"
        b"<codigoProcedimento>10101012</codigoProcedimento>"
        b"<valorPago>400.00</valorPago>"
        b"<dataPagamento>2026-08-25</dataPagamento>"
        b"</pagamento></pagamentos>"
    )
    files = {"file": ("glosa.xml", io.BytesIO(xml_bytes), "application/xml")}
    response = await client.post("/api/v1/ingestion/upload", files=files, data={"data_type": "glosa"}, headers=auth_headers_a)
    assert response.status_code == 201, response.text
    assert response.json()["error_row_count"] == 0

    billing = await _fetch_one(admin_engine, "SELECT * FROM core.billing WHERE tenant_id = :t", t=tenant_a)
    assert float(billing["received_value"]) == 400.0


async def test_glosa_upload_via_json(client, auth_headers_a, admin_engine, tenant_a):
    await _create_insurance_plan(admin_engine, tenant_a)
    fat_row = "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;500,00;20/08/2026;sadt;G-JSON-1;"
    await _upload_faturamento(client, auth_headers_a, fat_row)

    payload = [
        {
            "convenio": "Unimed Nacional",
            "guia_numero": "G-JSON-1",
            "codigo_procedimento": "10101012",
            "valor_pago": 450.0,
            "data_pagamento": "2026-08-25",
        }
    ]
    files = {"file": ("glosa.json", io.BytesIO(json.dumps(payload).encode()), "application/json")}
    response = await client.post("/api/v1/ingestion/upload", files=files, data={"data_type": "glosa"}, headers=auth_headers_a)
    assert response.status_code == 201, response.text
    assert response.json()["error_row_count"] == 0

    billing = await _fetch_one(admin_engine, "SELECT * FROM core.billing WHERE tenant_id = :t", t=tenant_a)
    assert float(billing["received_value"]) == 450.0


# Chave de conciliação ALTERNATIVA por numero_carteirinha (achado do
# Dicionário de Dados) — ver DECISÃO em RawDenialRow: usada quando o
# demonstrativo da operadora não traz guia_numero.


async def test_glosa_settles_by_member_card_when_guia_numero_is_absent(client, auth_headers_a, admin_engine, tenant_a):
    await _create_insurance_plan(admin_engine, tenant_a)
    fat_row = "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;500,00;20/08/2026;CART-001"
    fat = await _upload_faturamento_sem_guia(client, auth_headers_a, fat_row)
    assert fat.status_code == 201, fat.text
    assert fat.json()["error_row_count"] == 0

    glosa_row = "Unimed Nacional;CART-001;10101012;350,00;25/08/2026;;"
    resp = await _upload_glosa_por_carteirinha(client, auth_headers_a, glosa_row)
    assert resp.status_code == 201, resp.text
    assert resp.json()["error_row_count"] == 0

    billing = await _fetch_one(admin_engine, "SELECT * FROM core.billing WHERE tenant_id = :t", t=tenant_a)
    assert float(billing["received_value"]) == 350.0
    assert billing["status"] == "paid"

    glosa = await _fetch_one(admin_engine, "SELECT * FROM core.glosas WHERE tenant_id = :t", t=tenant_a)
    assert float(glosa["valor_glosado"]) == 150.0


async def test_glosa_row_without_guia_numero_or_member_card_is_rejected(client, auth_headers_a, admin_engine, tenant_a):
    """Nenhuma chave de conciliação preenchida — RawDenialRow rejeita a
    linha já na validação, antes de qualquer tentativa de casamento."""
    await _create_insurance_plan(admin_engine, tenant_a)
    glosa_row = "Unimed Nacional;;;300,00;25/08/2026;;"
    resp = await _upload_glosa(client, auth_headers_a, glosa_row)
    assert resp.status_code == 201, resp.text
    assert resp.json()["error_row_count"] == 1


async def test_glosa_row_with_unknown_member_card_is_rejected(client, auth_headers_a, admin_engine, tenant_a):
    await _create_insurance_plan(admin_engine, tenant_a)
    glosa_row = "Unimed Nacional;CART-INEXISTENTE;;300,00;25/08/2026;;"
    resp = await _upload_glosa_por_carteirinha(client, auth_headers_a, glosa_row)
    assert resp.status_code == 201, resp.text
    assert resp.json()["error_row_count"] == 1

    glosas = await _fetch_all(admin_engine, "SELECT * FROM core.glosas WHERE tenant_id = :t", t=tenant_a)
    assert glosas == []


async def test_glosa_ambiguous_member_card_without_procedure_code_is_rejected(client, auth_headers_a, admin_engine, tenant_a):
    """Mesma beneficiária com duas cobranças em aberto no mesmo convênio
    e o demonstrativo não trouxe codigo_procedimento para desambiguar —
    rejeita em vez de adivinhar (mesmo princípio da chave por guia)."""
    await _create_insurance_plan(admin_engine, tenant_a)
    rows = [
        "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;500,00;20/08/2026;CART-002",
        "12345678900;Paciente Teste;Unimed Nacional;20102030;J06;200,00;20/08/2026;CART-002",
    ]
    fat = await _upload_faturamento_sem_guia(client, auth_headers_a, *rows)
    assert fat.status_code == 201, fat.text
    assert fat.json()["error_row_count"] == 0

    glosa_row = "Unimed Nacional;CART-002;;700,00;25/08/2026;;"
    resp = await _upload_glosa_por_carteirinha(client, auth_headers_a, glosa_row)
    assert resp.status_code == 201, resp.text
    assert resp.json()["error_row_count"] == 1

    settled = await _fetch_all(
        admin_engine, "SELECT * FROM core.billing WHERE tenant_id = :t AND received_value IS NOT NULL", t=tenant_a
    )
    assert settled == []


async def test_glosa_disambiguates_member_card_by_procedure_code(client, auth_headers_a, admin_engine, tenant_a):
    await _create_insurance_plan(admin_engine, tenant_a)
    rows = [
        "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;500,00;20/08/2026;CART-003",
        "12345678900;Paciente Teste;Unimed Nacional;20102030;J06;200,00;20/08/2026;CART-003",
    ]
    await _upload_faturamento_sem_guia(client, auth_headers_a, *rows)

    glosa_row = "Unimed Nacional;CART-003;10101012;500,00;25/08/2026;;"
    resp = await _upload_glosa_por_carteirinha(client, auth_headers_a, glosa_row)
    assert resp.status_code == 201, resp.text
    assert resp.json()["error_row_count"] == 0

    settled = await _fetch_all(
        admin_engine, "SELECT * FROM core.billing WHERE tenant_id = :t AND received_value IS NOT NULL", t=tenant_a
    )
    assert len(settled) == 1
    assert float(settled[0]["charged_value"]) == 500.0

    untouched = await _fetch_all(
        admin_engine, "SELECT * FROM core.billing WHERE tenant_id = :t AND received_value IS NULL", t=tenant_a
    )
    assert len(untouched) == 1
    assert float(untouched[0]["charged_value"]) == 200.0


async def test_glosa_guia_numero_takes_precedence_over_member_card_when_both_present(
    client, auth_headers_a, admin_engine, tenant_a
):
    """Quando o demonstrativo traz os dois, guia_numero é a chave
    PRIMÁRIA (ver DECISÃO em RawDenialRow) — este teste garante que o
    caminho por guia continua sendo o escolhido, não uma race condition
    entre os dois."""
    await _create_insurance_plan(admin_engine, tenant_a)
    fat_row = "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;500,00;20/08/2026;sadt;G-PRIORIDADE;"
    fat = await _upload_faturamento(client, auth_headers_a, fat_row)
    assert fat.status_code == 201, fat.text

    glosa_row = "Unimed Nacional;G-PRIORIDADE;10101012;350,00;25/08/2026;;"
    resp = await _upload_glosa(client, auth_headers_a, glosa_row)
    assert resp.status_code == 201, resp.text
    assert resp.json()["error_row_count"] == 0

    billing = await _fetch_one(admin_engine, "SELECT * FROM core.billing WHERE tenant_id = :t", t=tenant_a)
    assert float(billing["received_value"]) == 350.0


# Achado 1 da Auditoria de Templates e Insights (crítico): normalização
# de numero_carteirinha — Faturamento e o demonstrativo de Glosa vêm de
# sistemas DIFERENTES e quase nunca formatam a carteirinha do mesmo
# jeito. Sem sanitização, a chave de conciliação alternativa falharia
# silenciosamente exatamente no cenário em que foi criada para ajudar.


async def test_glosa_matches_member_card_despite_different_formatting(client, auth_headers_a, admin_engine, tenant_a):
    """Faturamento grava a carteirinha com pontuação ("0012.345.678-90");
    o demonstrativo de Glosa traz a MESMA carteirinha sem pontuação e com
    espaço interno ("0012 345 678 90"). Antes da normalização, a busca
    por string exata não encontraria nada."""
    await _create_insurance_plan(admin_engine, tenant_a)
    fat_row = "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;500,00;20/08/2026;0012.345.678-90"
    fat = await _upload_faturamento_sem_guia(client, auth_headers_a, fat_row)
    assert fat.status_code == 201, fat.text
    assert fat.json()["error_row_count"] == 0

    glosa_row = "Unimed Nacional;0012 345 678 90;10101012;350,00;25/08/2026;;"
    resp = await _upload_glosa_por_carteirinha(client, auth_headers_a, glosa_row)
    assert resp.status_code == 201, resp.text
    assert resp.json()["error_row_count"] == 0

    billing = await _fetch_one(admin_engine, "SELECT * FROM core.billing WHERE tenant_id = :t", t=tenant_a)
    assert float(billing["received_value"]) == 350.0
    assert billing["member_card_number"] == "001234567890"  # normalizado: só alfanumérico, caixa alta


# Achado 6 da Auditoria de Templates e Insights (médio): confirmação
# cruzada de identidade — só entra em jogo quando o demonstrativo traz
# CPF do beneficiário.


async def test_glosa_member_card_with_matching_cpf_settles_normally(client, auth_headers_a, admin_engine, tenant_a):
    await _create_insurance_plan(admin_engine, tenant_a)
    fat_row = "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;500,00;20/08/2026;CART-CPF-1"
    fat = await _upload_faturamento_sem_guia(client, auth_headers_a, fat_row)
    assert fat.status_code == 201, fat.text

    glosa_row = "Unimed Nacional;CART-CPF-1;123.456.789-00;10101012;350,00;25/08/2026;;"
    resp = await _upload_glosa_por_carteirinha_com_cpf(client, auth_headers_a, glosa_row)
    assert resp.status_code == 201, resp.text
    assert resp.json()["error_row_count"] == 0

    billing = await _fetch_one(admin_engine, "SELECT * FROM core.billing WHERE tenant_id = :t", t=tenant_a)
    assert float(billing["received_value"]) == 350.0


async def test_glosa_member_card_with_mismatched_cpf_is_rejected(client, auth_headers_a, admin_engine, tenant_a):
    """A carteirinha bateu, mas o CPF do demonstrativo é de OUTRA pessoa
    — sinal real de que a linha pode estar casando com o paciente
    errado (erro de digitação na carteirinha, por exemplo). Rejeita em
    vez de liquidar às cegas."""
    await _create_insurance_plan(admin_engine, tenant_a)
    fat_row = "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;500,00;20/08/2026;CART-CPF-2"
    fat = await _upload_faturamento_sem_guia(client, auth_headers_a, fat_row)
    assert fat.status_code == 201, fat.text

    glosa_row = "Unimed Nacional;CART-CPF-2;999.999.999-99;10101012;350,00;25/08/2026;;"
    resp = await _upload_glosa_por_carteirinha_com_cpf(client, auth_headers_a, glosa_row)
    assert resp.status_code == 201, resp.text
    assert resp.json()["error_row_count"] == 1

    billing = await _fetch_one(admin_engine, "SELECT * FROM core.billing WHERE tenant_id = :t", t=tenant_a)
    assert billing["received_value"] is None  # NÃO foi liquidado


async def test_glosa_member_card_without_cpf_in_file_settles_normally(client, auth_headers_a, admin_engine, tenant_a):
    """Sem CPF nenhum no demonstrativo (arquivo antigo, ou operadora que
    não envia isso) — a confirmação cruzada é opcional, nunca bloqueia
    quem simplesmente não manda esse dado."""
    await _create_insurance_plan(admin_engine, tenant_a)
    fat_row = "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;500,00;20/08/2026;CART-CPF-3"
    fat = await _upload_faturamento_sem_guia(client, auth_headers_a, fat_row)
    assert fat.status_code == 201, fat.text

    glosa_row = "Unimed Nacional;CART-CPF-3;10101012;350,00;25/08/2026;;"
    resp = await _upload_glosa_por_carteirinha(client, auth_headers_a, glosa_row)
    assert resp.status_code == 201, resp.text
    assert resp.json()["error_row_count"] == 0
