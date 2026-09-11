"""
tests/integration/test_billing_denial_engine.py

Ponta a ponta via HTTP: cria paciente -> convênio -> contrato -> consulta
-> fatura, e verifica que o denial_risk_engine (testado isoladamente em
tests/test_denial_risk_engine.py) também funciona quando chamado através
da pilha real (endpoint -> service -> repository -> banco).
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Unimed Nacional", normalized_key="unimed_nacional") -> str:
    import uuid

    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) "
                "VALUES (:id, :t, :name, :key)"
            ),
            {"id": plan_id, "t": tenant_id, "name": display_name, "key": normalized_key},
        )
    return plan_id


async def _create_contract(admin_engine, tenant_id, plan_id, procedure_code="10101012", agreed_value=150.0):
    """Cria o cabeçalho (contracts, já HOMOLOGADO) + um item de preço
    (contract_items) — ver DECISÃO em app/sql/007_contract_intelligence.sql
    sobre por que contracts deixou de carregar procedure_code/agreed_value
    direto na linha."""
    import uuid

    contract_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.contracts (id, tenant_id, insurance_plan_id, valid_from, status) "
                "VALUES (:id, :t, :plan, '2026-01-01', 'homologado')"
            ),
            {"id": contract_id, "t": tenant_id, "plan": plan_id},
        )
        await conn.execute(
            text(
                "INSERT INTO core.contract_items (tenant_id, contract_id, tuss_code, agreed_price) "
                "VALUES (:t, :contract, :code, :value)"
            ),
            {"t": tenant_id, "contract": contract_id, "code": procedure_code, "value": agreed_value},
        )
    return contract_id


async def test_billing_with_missing_cid_is_flagged_high_risk_and_held(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_contract(admin_engine, tenant_a, plan_id, procedure_code="10101012", agreed_value=150.0)

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Glosa"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
            # cid_code OMITIDO de propósito — gatilho clássico de alto risco
        },
        headers=auth_headers_a,
    )
    assert appointment_resp.status_code == 201
    appointment_id = appointment_resp.json()["id"]

    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 150.0},
        headers=auth_headers_a,
    )
    assert billing_resp.status_code == 201
    body = billing_resp.json()
    assert body["denial_risk_level"] == "high"
    assert "missing_cid" in body["denial_reasons"]
    assert body["status"] == "held_for_review"


async def test_billing_above_contract_value_reports_value_saved(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_contract(admin_engine, tenant_a, plan_id, procedure_code="20202020", agreed_value=150.0)

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Overcharge"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "20202020",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]

    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 180.0},
        headers=auth_headers_a,
    )
    assert billing_resp.status_code == 201
    body = billing_resp.json()
    assert body["denial_risk_level"] == "high"
    assert "value_above_contract" in body["denial_reasons"]
    assert body["value_saved_by_correction"] == pytest.approx(30.0)


async def test_clean_billing_is_low_risk_and_not_held(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_contract(admin_engine, tenant_a, plan_id, procedure_code="30303030", agreed_value=150.0)

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Limpo"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "30303030",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]

    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 150.0},
        headers=auth_headers_a,
    )
    body = billing_resp.json()
    assert body["denial_risk_level"] == "low"
    assert body["status"] == "pending"


async def test_list_high_risk_billing_returns_only_held_for_review(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a, display_name="Bradesco", normalized_key="bradesco")
    await _create_contract(admin_engine, tenant_a, plan_id, procedure_code="70707070", agreed_value=150.0)

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Alto Risco"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    # Um appointment SEM cid_code (vira alto risco) e outro limpo (vira baixo risco)
    high_risk_appt = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "70707070",
        },
        headers=auth_headers_a,
    )
    clean_appt = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
            "procedure_code": "70707070",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )

    await client.post(
        "/api/v1/billing",
        json={"appointment_id": high_risk_appt.json()["id"], "insurance_plan_id": plan_id, "charged_value": 150.0},
        headers=auth_headers_a,
    )
    await client.post(
        "/api/v1/billing",
        json={"appointment_id": clean_appt.json()["id"], "insurance_plan_id": plan_id, "charged_value": 150.0},
        headers=auth_headers_a,
    )

    high_risk_list = await client.get("/api/v1/billing/high-risk", headers=auth_headers_a)
    assert high_risk_list.status_code == 200
    # BUG DE TESTE CORRIGIDO: /billing/high-risk devolve o envelope
    # paginado {items, total, limit, offset} (ver PaginatedResponse em
    # app/schemas/pagination.py), não uma lista crua — este teste nunca
    # foi atualizado quando a paginação chegou a este endpoint.
    items = high_risk_list.json()["items"]
    reasons = [item["denial_reasons"] for item in items]
    assert len(items) == 1
    assert "missing_cid" in reasons[0]


async def test_list_high_risk_billing_filters_by_insurance_plan_id(client, auth_headers_a, admin_engine, tenant_a):
    """Sala de Comando 2.0, item 4 do roadmap ("botão de ação real"): o
    insight de recusa em alta agora linka pra aqui já filtrado pelo
    convênio exato — este teste prova que o filtro de verdade restringe
    a fila, não só decora a URL sem efeito."""
    plan_a = await _create_insurance_plan(admin_engine, tenant_a, display_name="Bradesco", normalized_key="bradesco")
    plan_b = await _create_insurance_plan(admin_engine, tenant_a, display_name="Amil", normalized_key="amil")
    await _create_contract(admin_engine, tenant_a, plan_a, procedure_code="70707070", agreed_value=150.0)
    await _create_contract(admin_engine, tenant_a, plan_b, procedure_code="80808080", agreed_value=150.0)

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Dois Convênios"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    async def _create_high_risk_billing(plan_id: str, procedure_code: str) -> None:
        appt = await client.post(
            "/api/v1/appointments",
            json={
                "patient_id": patient_id,
                "insurance_plan_id": plan_id,
                "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                "procedure_code": procedure_code,
                # cid_code omitido -> alto risco
            },
            headers=auth_headers_a,
        )
        await client.post(
            "/api/v1/billing",
            json={"appointment_id": appt.json()["id"], "insurance_plan_id": plan_id, "charged_value": 150.0},
            headers=auth_headers_a,
        )

    await _create_high_risk_billing(plan_a, "70707070")
    await _create_high_risk_billing(plan_b, "80808080")

    unfiltered = await client.get("/api/v1/billing/high-risk", headers=auth_headers_a)
    assert unfiltered.json()["total"] == 2

    filtered = await client.get(f"/api/v1/billing/high-risk?insurance_plan_id={plan_a}", headers=auth_headers_a)
    assert filtered.status_code == 200
    body = filtered.json()
    assert body["total"] == 1
    assert body["items"][0]["charged_value"] == 150.0


# Achados 10/11 da Auditoria de Templates e Insights: BillingCreateRequest
# (endpoint manual, POST /billing) grava as MESMAS colunas
# (member_card_number/item_type) que o Template de Faturamento (ingestão
# em massa) já valida desde a Rodada 1 — mas sem a mesma blindagem. Os 3
# testes abaixo provam que o endpoint manual agora tem paridade.


async def test_manual_billing_sanitizes_member_card_number(client, auth_headers_a, admin_engine, tenant_a):
    """Achado 10 (alto) — mesmo cenário do achado: carteirinha digitada
    com pontuação/espaço precisa ser normalizada igual à ingestão, senão
    um demonstrativo de Glosa que chegar depois (já normalizado) nunca
    vai casar com este billing."""
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_contract(admin_engine, tenant_a, plan_id, procedure_code="10101012", agreed_value=150.0)

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Carteirinha Manual"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    appt = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )
    resp = await client.post(
        "/api/v1/billing",
        json={
            "appointment_id": appt.json()["id"],
            "insurance_plan_id": plan_id,
            "charged_value": 150.0,
            "member_card_number": "0012.345.678-90",
        },
        headers=auth_headers_a,
    )
    assert resp.status_code == 201, resp.text

    async with admin_engine.begin() as conn:
        result = await conn.execute(text("SELECT member_card_number FROM core.billing WHERE tenant_id = :t"), {"t": tenant_a})
        row = result.mappings().first()
    assert row["member_card_number"] == "001234567890"  # normalizado: só alfanumérico, caixa alta


async def test_manual_billing_rejects_invalid_item_type_with_422(client, auth_headers_a, admin_engine, tenant_a):
    """Achado 11 (baixo) — antes desta correção, um item_type fora do
    vocabulário fechado passava pelo Pydantic sem erro e só era pego
    pelo CHECK constraint do banco (500 opaco). Agora é rejeitado cedo,
    com uma mensagem que nomeia os valores aceitos."""
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_contract(admin_engine, tenant_a, plan_id, procedure_code="10101012", agreed_value=150.0)

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Item Type Inválido"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    appt = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )
    resp = await client.post(
        "/api/v1/billing",
        json={
            "appointment_id": appt.json()["id"],
            "insurance_plan_id": plan_id,
            "charged_value": 150.0,
            "item_type": "tipo-inexistente",
        },
        headers=auth_headers_a,
    )
    assert resp.status_code == 422, resp.text
    assert "não reconhecido" in resp.text


async def test_manual_billing_normalizes_valid_item_type(client, auth_headers_a, admin_engine, tenant_a):
    """Mesmo vocabulário fechado aceita variações de grafia (maiúscula,
    espaço em vez de underscore) — mesmo comportamento já garantido na
    ingestão, agora também no endpoint manual."""
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_contract(admin_engine, tenant_a, plan_id, procedure_code="10101012", agreed_value=150.0)

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Item Type Válido"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    appt = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )
    resp = await client.post(
        "/api/v1/billing",
        json={
            "appointment_id": appt.json()["id"],
            "insurance_plan_id": plan_id,
            "charged_value": 150.0,
            "item_type": "Material OPME",
        },
        headers=auth_headers_a,
    )
    assert resp.status_code == 201, resp.text

    async with admin_engine.begin() as conn:
        result = await conn.execute(text("SELECT item_type FROM core.billing WHERE tenant_id = :t"), {"t": tenant_a})
        row = result.mappings().first()
    assert row["item_type"] == "material_opme"
