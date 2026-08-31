"""tests/integration/test_integrations.py — Central de Integrações &
Webhooks: emissão/revogação de chaves de API por tenant."""


async def test_owner_can_create_list_and_revoke_api_key(client, auth_headers_a):
    create_resp = await client.post("/api/v1/integrations/api-keys", json={"name": "ERP Produção"}, headers=auth_headers_a)
    assert create_resp.status_code == 201
    body = create_resp.json()
    assert body["api_key"].startswith("iarcm_")
    assert body["key_prefix"] == body["api_key"][:12]
    key_id = body["id"]

    list_resp = await client.get("/api/v1/integrations/api-keys", headers=auth_headers_a)
    assert list_resp.status_code == 200
    assert all("api_key" not in item for item in list_resp.json())  # nunca reexibe o segredo
    assert any(item["id"] == key_id for item in list_resp.json())

    revoke_resp = await client.delete(f"/api/v1/integrations/api-keys/{key_id}", headers=auth_headers_a)
    assert revoke_resp.status_code == 200
    assert revoke_resp.json()["revoked_at"] is not None


async def test_atendimento_cannot_manage_api_keys(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao2@clinica-a.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post("/api/v1/integrations/api-keys", json={"name": "Tentativa indevida"}, headers=headers)
    assert resp.status_code == 403


async def test_tenant_b_cannot_see_tenant_a_api_keys(client, auth_headers_a, auth_headers_b):
    await client.post("/api/v1/integrations/api-keys", json={"name": "Chave da Clínica A"}, headers=auth_headers_a)

    list_b = await client.get("/api/v1/integrations/api-keys", headers=auth_headers_b)
    assert list_b.status_code == 200
    assert list_b.json() == []
