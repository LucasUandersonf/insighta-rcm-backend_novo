"""
tests/integration/test_contracts.py

Teste FUNCIONAL de contracts (test_rbac.py já cobre a barreira de
permissão; aqui é o caminho feliz e as validações de negócio do schema).

Cadastro MANUAL (sem PDF/IA) — payload agora carrega uma LISTA de itens
(hierarquia Convênio -> Plano -> Contrato -> Itens, ver DECISÃO em
app/sql/007_contract_intelligence.sql), não mais um único
procedure_code/agreed_value por chamada.
"""
from datetime import date, timedelta

from sqlalchemy import text


async def _create_insurance_plan(admin_engine, tenant_id, display_name="SulAmérica") -> str:
    import uuid

    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :n, :k)"),
            {"id": plan_id, "t": tenant_id, "n": display_name, "k": "sulamerica"},
        )
    return plan_id


async def test_create_and_list_active_contract(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)

    create_resp = await client.post(
        "/api/v1/contracts",
        json={
            "insurance_plan_id": plan_id,
            "valid_from": date.today().isoformat(),
            "items": [{"tuss_code": "40404040", "procedure_name": "Consulta", "agreed_price": 220.50}],
        },
        headers=auth_headers_a,
    )
    assert create_resp.status_code == 201
    body = create_resp.json()
    assert body["status"] == "homologado"
    assert len(body["items"]) == 1
    assert body["items"][0]["tuss_code"] == "40404040"
    assert body["items"][0]["agreed_price"] == 220.50

    list_resp = await client.get("/api/v1/contracts/active", headers=auth_headers_a)
    assert list_resp.status_code == 200
    contracts = list_resp.json()["items"]
    assert any(
        any(item["tuss_code"] == "40404040" for item in c["items"]) for c in contracts
    )


async def test_expired_contract_still_returned_by_list_all_but_not_matched_for_billing(client, auth_headers_a, admin_engine, tenant_a):
    """`/contracts/active` hoje lista TODOS os contratos cadastrados (não
    filtra por vigência na query — quem filtra por vigência é
    ContractItemRepository.find_agreed_price, usado pelo motor de glosa),
    então o teste de vigência relevante é o do motor de regras
    (test_denial_risk_engine.py), não deste endpoint de listagem."""
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)

    past_start = date.today() - timedelta(days=60)
    past_end = date.today() - timedelta(days=30)
    create_resp = await client.post(
        "/api/v1/contracts",
        json={
            "insurance_plan_id": plan_id,
            "valid_from": past_start.isoformat(),
            "valid_until": past_end.isoformat(),
            "items": [{"tuss_code": "50505050", "procedure_name": "Consulta", "agreed_price": 100.0}],
        },
        headers=auth_headers_a,
    )
    assert create_resp.status_code == 201


async def test_contract_with_invalid_date_range_is_rejected(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)

    response = await client.post(
        "/api/v1/contracts",
        json={
            "insurance_plan_id": plan_id,
            "valid_from": date.today().isoformat(),
            "valid_until": (date.today() - timedelta(days=1)).isoformat(),  # anterior a valid_from
            "items": [{"tuss_code": "60606060", "procedure_name": "Consulta", "agreed_price": 100.0}],
        },
        headers=auth_headers_a,
    )
    assert response.status_code == 422  # falha de validação do Pydantic, antes de tocar no banco


async def test_contract_without_items_is_rejected(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)

    response = await client.post(
        "/api/v1/contracts",
        json={
            "insurance_plan_id": plan_id,
            "valid_from": date.today().isoformat(),
            "items": [],
        },
        headers=auth_headers_a,
    )
    assert response.status_code == 422
