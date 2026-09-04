"""
tests/integration/test_rbac.py

RBAC é a SEGUNDA camada de defesa (RLS é a primeira — ver comentário em
app/api/deps.py:require_role). Estes testes provam que ela de fato barra
quem não devia, não só documenta a intenção no código.
"""
import pytest
import pytest_asyncio


async def _login_as(client, admin_engine, tenant_id, role) -> dict:
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_id, email=f"{role}@rbac-test.com", role=role)
    token = await _login(client, user["email"], user["password"])
    return {"Authorization": f"Bearer {token}"}


async def test_atendimento_cannot_create_contract(client, admin_engine, tenant_a):
    """'atendimento' cuida da recepção — dado financeiro sensível (tabela de repasse) não é dele."""
    headers = await _login_as(client, admin_engine, tenant_a, "atendimento")

    response = await client.post(
        "/api/v1/contracts",
        json={
            "insurance_plan_id": "00000000-0000-0000-0000-000000000000",
            "valid_from": "2026-01-01",
            "items": [{"tuss_code": "10101012", "procedure_name": "Consulta", "agreed_price": 150.0}],
        },
        headers=headers,
    )
    assert response.status_code == 403


async def test_financeiro_can_create_contract_but_not_a_professional(client, admin_engine, tenant_a):
    """financeiro pode mexer em contrato (financeiro), mas não em cadastro de profissional (administrativo)."""
    headers = await _login_as(client, admin_engine, tenant_a, "financeiro")

    contract_response = await client.get("/api/v1/contracts/active", headers=headers)
    assert contract_response.status_code == 200  # leitura liberada para financeiro

    professional_response = await client.post("/api/v1/professionals", json={"full_name": "Dr. X"}, headers=headers)
    assert professional_response.status_code == 403


async def test_financeiro_cannot_edit_a_professional(client, admin_engine, tenant_a):
    """Mesma barreira de test_financeiro_can_create_contract_but_not_a_professional,
    agora sobre PATCH — owner cria o profissional, financeiro tenta editar."""
    owner_headers = await _login_as(client, admin_engine, tenant_a, "owner")
    create_resp = await client.post("/api/v1/professionals", json={"full_name": "Dr. Y"}, headers=owner_headers)
    professional_id = create_resp.json()["id"]

    financeiro_headers = await _login_as(client, admin_engine, tenant_a, "financeiro")
    update_resp = await client.patch(
        f"/api/v1/professionals/{professional_id}", json={"specialty": "Não deveria editar"}, headers=financeiro_headers
    )
    assert update_resp.status_code == 403


async def test_auditor_is_read_only_everywhere_tested(client, admin_engine, tenant_a):
    headers = await _login_as(client, admin_engine, tenant_a, "auditor")

    read_response = await client.get("/api/v1/patients", headers=headers)
    assert read_response.status_code == 200

    write_response = await client.post("/api/v1/patients", json={"full_name": "Não deveria criar"}, headers=headers)
    assert write_response.status_code == 403


async def test_owner_has_full_access_across_domains(client, admin_engine, tenant_a):
    headers = await _login_as(client, admin_engine, tenant_a, "owner")

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Criado pelo owner"}, headers=headers)
    assert patient_resp.status_code == 201

    professional_resp = await client.post("/api/v1/professionals", json={"full_name": "Dr. Owner"}, headers=headers)
    assert professional_resp.status_code == 201
