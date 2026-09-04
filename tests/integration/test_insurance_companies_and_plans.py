"""
tests/integration/test_insurance_companies_and_plans.py

Cobre o gap achado pelo usuário: Convênio (InsuranceCompany) e Plano
(InsurancePlan) não tinham NENHUMA forma de "sair" do cadastro — nem
exclusão, nem desativação — mesmo sendo referenciados por Contract/
Appointment/Billing (exclusão de verdade quebraria essas FKs). Este é o
primeiro teste de integração para app/api/v1/endpoints/insurance_companies.py
— endpoint sem cobertura alguma até aqui (confirmado via busca antes de
escrever isto).
"""
from sqlalchemy import text


async def test_create_and_list_company_defaults_to_active_only(client, auth_headers_a, admin_engine, tenant_a):
    create_resp = await client.post(
        "/api/v1/insurance-companies",
        json={"name": "Amil", "ans_registry": "12345"},
        headers=auth_headers_a,
    )
    assert create_resp.status_code == 201, create_resp.text
    company = create_resp.json()
    assert company["is_active"] is True

    deactivate_resp = await client.patch(
        f"/api/v1/insurance-companies/{company['id']}", json={"is_active": False}, headers=auth_headers_a
    )
    assert deactivate_resp.status_code == 200, deactivate_resp.text
    assert deactivate_resp.json()["is_active"] is False

    # Desativada: some da listagem padrão (a que alimenta seletores de
    # cadastro novo), mas continua existindo — sem exclusão de verdade.
    default_list = (await client.get("/api/v1/insurance-companies", headers=auth_headers_a)).json()
    assert company["id"] not in {c["id"] for c in default_list}

    full_list = (await client.get("/api/v1/insurance-companies?include_inactive=true", headers=auth_headers_a)).json()
    assert company["id"] in {c["id"] for c in full_list}

    reactivate_resp = await client.patch(
        f"/api/v1/insurance-companies/{company['id']}", json={"is_active": True}, headers=auth_headers_a
    )
    assert reactivate_resp.status_code == 200
    assert reactivate_resp.json()["is_active"] is True
    default_list_after = (await client.get("/api/v1/insurance-companies", headers=auth_headers_a)).json()
    assert company["id"] in {c["id"] for c in default_list_after}


async def test_deactivating_company_does_not_wipe_appeal_deadline(client, auth_headers_a):
    """
    BUG CORRIGIDO — antes desta mudança, update_company reatribuía
    `default_appeal_deadline_days` incondicionalmente com o valor do
    payload (sempre None quando o campo não é enviado), então um PATCH
    só de `is_active` apagaria silenciosamente um prazo já configurado.
    """
    create_resp = await client.post(
        "/api/v1/insurance-companies",
        json={"name": "Bradesco Saúde", "default_appeal_deadline_days": 30},
        headers=auth_headers_a,
    )
    company_id = create_resp.json()["id"]

    deactivate_resp = await client.patch(
        f"/api/v1/insurance-companies/{company_id}", json={"is_active": False}, headers=auth_headers_a
    )
    assert deactivate_resp.status_code == 200
    assert deactivate_resp.json()["default_appeal_deadline_days"] == 30
    assert deactivate_resp.json()["is_active"] is False


async def _create_company(client, auth_headers, name="Unimed") -> str:
    resp = await client.post("/api/v1/insurance-companies", json={"name": name}, headers=auth_headers)
    return resp.json()["id"]


async def test_create_and_list_plan_defaults_to_active_only(client, auth_headers_a):
    company_id = await _create_company(client, auth_headers_a)

    create_resp = await client.post(
        "/api/v1/insurance-companies/plans",
        json={"insurance_company_id": company_id, "display_name": "Unimed Nacional"},
        headers=auth_headers_a,
    )
    assert create_resp.status_code == 201, create_resp.text
    plan = create_resp.json()
    assert plan["is_active"] is True
    assert plan["normalized_key"] == "unimed_nacional"

    deactivate_resp = await client.patch(
        f"/api/v1/insurance-companies/plans/{plan['id']}", json={"is_active": False}, headers=auth_headers_a
    )
    assert deactivate_resp.status_code == 200, deactivate_resp.text
    assert deactivate_resp.json()["is_active"] is False

    default_list = (await client.get("/api/v1/insurance-companies/plans", headers=auth_headers_a)).json()
    assert plan["id"] not in {p["id"] for p in default_list}

    full_list = (
        await client.get("/api/v1/insurance-companies/plans?include_inactive=true", headers=auth_headers_a)
    ).json()
    assert plan["id"] in {p["id"] for p in full_list}


async def test_deactivated_plan_still_resolves_during_ingestion(client, auth_headers_a, admin_engine, tenant_a, monkeypatch):
    """
    DECISÃO deliberada (ver comentário em InsurancePlan.is_active):
    desativar um plano só o esconde dos seletores de cadastro NOVO — não
    afeta InsurancePlanRepository.resolve(), usado pela normalização de
    ingestão. Um arquivo do ERP que ainda cita o convênio pelo nome
    continua reconciliando normalmente contra um plano desativado (mesmo
    princípio já valia para Professional.is_active nunca ter filtrado
    get_by_registry/get_by_name). Mesmo mock de fronteira de rede de
    test_ingestion_upload.py.
    """
    import io
    from datetime import date, timedelta

    from app.services import ingestion_storage_client as storage_module

    monkeypatch.setattr(storage_module.settings, "AWS_S3_INGEST_BUCKET", "bucket-teste-plano-inativo")

    async def _fake_upload_bytes(self, *, key: str, raw_bytes: bytes) -> str | None:
        return None

    monkeypatch.setattr(storage_module.IngestionStorageClient, "upload_bytes", _fake_upload_bytes)

    company_id = await _create_company(client, auth_headers_a, "SulAmérica")
    plan_resp = await client.post(
        "/api/v1/insurance-companies/plans",
        json={"insurance_company_id": company_id, "display_name": "SulAmérica Direto"},
        headers=auth_headers_a,
    )
    plan_id = plan_resp.json()["id"]
    await client.patch(f"/api/v1/insurance-companies/plans/{plan_id}", json={"is_active": False}, headers=auth_headers_a)

    service_date = (date.today() - timedelta(days=1)).strftime("%d/%m/%Y")
    header = "cpf_paciente;nome_paciente;convenio;codigo_procedimento;cid;valor_cobrado;data_atendimento"
    row = f"11122233344;Paciente Teste;SulAmérica Direto;10101012;J06;150,00;{service_date}"
    csv_bytes = (header + "\r\n" + row + "\r\n").encode("utf-8-sig")
    upload_resp = await client.post(
        "/api/v1/ingestion/upload",
        files={"file": ("lote.csv", io.BytesIO(csv_bytes), "text/csv")},
        headers=auth_headers_a,
    )

    assert upload_resp.status_code == 201, upload_resp.text
    # row_count=1 e error_row_count=0 provam que a linha foi normalizada
    # com sucesso — se o plano desativado bloqueasse resolve(), esta
    # linha teria caído em "unknown_insurance_plan" (error_row_count=1).
    assert upload_resp.json()["row_count"] == 1
    assert upload_resp.json()["error_row_count"] == 0
