"""
tests/integration/test_ingestion_resolution.py

O worker que normalmente gera linhas `rejected` (app/worker/ingestion_worker.py)
não roda em teste HTTP — semeamos a linha rejeitada direto no banco,
simulando exatamente o estado que o worker deixaria para trás, e testamos
só a parte do fluxo que É um endpoint HTTP: listar e resolver
manualmente.
"""
import json
import uuid

from sqlalchemy import text


async def _seed_rejected_row(admin_engine, tenant_id, *, raw_value="UNIMED NAC.", patient_name="Paciente Importado") -> int:
    file_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        # BUG DE TESTE CORRIGIDO: s3_key era um literal fixo — duas
        # chamadas a este helper para o MESMO tenant (como o teste de
        # resolução em lote faz) violavam
        # uq_ingestion_files_idempotency_null_version (tenant_id,
        # s3_bucket, s3_key), já que dois ingestion_files diferentes
        # nunca poderiam apontar para a mesma chave. Cada arquivo
        # "importado" pelo teste agora tem uma chave própria.
        await conn.execute(
            text(
                "INSERT INTO core.ingestion_files (id, tenant_id, s3_bucket, s3_key, file_format, status) "
                "VALUES (:id, :t, 'bucket-teste', :key, 'csv', 'processed')"
            ),
            {"id": file_id, "t": tenant_id, "key": f"tenants/x/incoming/csv/arquivo-{file_id}.csv"},
        )
        payload = {
            "patient_cpf": "12345678900",
            "patient_name": patient_name,
            "insurance_plan_raw_name": raw_value,
            "procedure_code": "10101012",
            "cid_code": "J06",
            "charged_value": 150.0,
            "service_date": "2026-08-20",
        }
        result = await conn.execute(
            text(
                "INSERT INTO core.ingestion_raw_rows "
                "(tenant_id, ingestion_file_id, row_number, payload, validation_errors, status) "
                "VALUES (:t, :file_id, 1, CAST(:payload AS JSONB), CAST(:errors AS JSONB), 'rejected') "
                "RETURNING id"
            ),
            {
                "t": tenant_id,
                "file_id": file_id,
                "payload": json.dumps(payload),
                "errors": json.dumps({"reason": "unknown_insurance_plan", "raw_value": raw_value}),
            },
        )
        return result.scalar_one()


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Unimed Nacional", normalized_key="unimed_nacional") -> str:
    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :n, :k)"),
            {"id": plan_id, "t": tenant_id, "n": display_name, "k": normalized_key},
        )
    return plan_id


async def test_list_rejected_rows_filters_by_reason(client, auth_headers_a, admin_engine, tenant_a):
    await _seed_rejected_row(admin_engine, tenant_a)

    response = await client.get("/api/v1/ingestion/rejected", params={"reason": "unknown_insurance_plan"}, headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["raw_value"] == "UNIMED NAC."


async def test_resolve_unknown_insurance_plan_promotes_the_row(client, auth_headers_a, admin_engine, tenant_a):
    row_id = await _seed_rejected_row(admin_engine, tenant_a)
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)

    response = await client.post(
        f"/api/v1/ingestion/rejected/{row_id}/resolve-insurance-plan",
        json={"insurance_plan_id": plan_id},
        headers=auth_headers_a,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["resolved"] is True

    # A linha não deve mais aparecer como rejeitada.
    still_rejected = await client.get("/api/v1/ingestion/rejected", headers=auth_headers_a)
    assert still_rejected.json() == []

    # E um billing de verdade deve ter sido criado a partir dela.
    high_risk_or_pending = await client.get("/api/v1/billing/high-risk", headers=auth_headers_a)
    assert high_risk_or_pending.status_code == 200  # não afirma conteúdo específico aqui, só que o endpoint responde


async def test_resolving_one_row_also_resolves_other_rows_with_same_raw_value(client, auth_headers_a, admin_engine, tenant_a):
    """O comportamento de resolução em lote documentado em normalization_service.py."""
    row_id_1 = await _seed_rejected_row(admin_engine, tenant_a, raw_value="UNIMED NAC.", patient_name="Paciente 1")
    row_id_2 = await _seed_rejected_row(admin_engine, tenant_a, raw_value="UNIMED NAC.", patient_name="Paciente 2")
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)

    response = await client.post(
        f"/api/v1/ingestion/rejected/{row_id_1}/resolve-insurance-plan",
        json={"insurance_plan_id": plan_id},
        headers=auth_headers_a,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["resolved"] is True
    assert body["additionally_resolved_count"] == 1  # a segunda linha (row_id_2) foi junto

    remaining = await client.get("/api/v1/ingestion/rejected", headers=auth_headers_a)
    assert remaining.json() == []


async def test_resolving_with_nonexistent_plan_returns_404(client, auth_headers_a, admin_engine, tenant_a):
    row_id = await _seed_rejected_row(admin_engine, tenant_a)

    response = await client.post(
        f"/api/v1/ingestion/rejected/{row_id}/resolve-insurance-plan",
        json={"insurance_plan_id": "00000000-0000-0000-0000-000000000000"},
        headers=auth_headers_a,
    )
    assert response.status_code == 404
