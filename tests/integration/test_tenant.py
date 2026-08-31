"""tests/integration/test_tenant.py — Painel do Administrador da Empresa:
dados cadastrais e plano do tenant. Também prova isolamento: owner da
Clínica A nunca vê/edita dados da Clínica B (mesmo raciocínio de
test_rls_isolation.py, aplicado à única tabela sem RLS por linha — o
isolamento aqui vem do service sempre usar current_user.tenant_id, nunca
de um id recebido por parâmetro)."""


async def test_owner_can_view_and_update_own_tenant(client, auth_headers_a):
    get_resp = await client.get("/api/v1/tenant", headers=auth_headers_a)
    assert get_resp.status_code == 200
    assert get_resp.json()["trade_name"] == "Clínica A"

    patch_resp = await client.patch("/api/v1/tenant", json={"trade_name": "Clínica A Renomeada"}, headers=auth_headers_a)
    assert patch_resp.status_code == 200
    assert patch_resp.json()["trade_name"] == "Clínica A Renomeada"


async def test_non_owner_cannot_update_tenant(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="financeiro@clinica-a.com", role="financeiro")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.patch("/api/v1/tenant", json={"trade_name": "Não deveria"}, headers=headers)
    assert resp.status_code == 403


async def test_tenant_b_never_sees_tenant_a_data(client, auth_headers_a, auth_headers_b):
    resp_a = await client.get("/api/v1/tenant", headers=auth_headers_a)
    resp_b = await client.get("/api/v1/tenant", headers=auth_headers_b)

    assert resp_a.json()["trade_name"] == "Clínica A"
    assert resp_b.json()["trade_name"] == "Clínica B"
    assert resp_a.json()["id"] != resp_b.json()["id"]


async def test_list_available_plans(client, auth_headers_a):
    resp = await client.get("/api/v1/tenant/plans/available", headers=auth_headers_a)
    assert resp.status_code == 200
    assert "starter" in resp.json()
