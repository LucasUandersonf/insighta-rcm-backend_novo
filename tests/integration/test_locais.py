"""
tests/integration/test_locais.py

Fase 4 do plano de adequação ao fluxo real de mercado (Agendamento ->
Atendimento -> Faturamento — ver conversa/PLANO_ADEQUACAO_TISS.md):
Local de Atendimento (Unidade/Setor) como catálogo próprio, com o
mesmo padrão de desativação (não exclusão) já usado em
Professional/InsuranceCompany/InsurancePlan.
"""


async def test_create_and_list_local_defaults_to_active_only(client, auth_headers_a):
    create_resp = await client.post("/api/v1/locais", json={"nome": "Pronto Socorro Adulto"}, headers=auth_headers_a)
    assert create_resp.status_code == 201, create_resp.text
    local = create_resp.json()
    assert local["nome"] == "Pronto Socorro Adulto"
    assert local["is_active"] is True

    deactivate_resp = await client.patch(f"/api/v1/locais/{local['id']}", json={"is_active": False}, headers=auth_headers_a)
    assert deactivate_resp.status_code == 200
    assert deactivate_resp.json()["is_active"] is False

    default_list = (await client.get("/api/v1/locais", headers=auth_headers_a)).json()
    assert local["id"] not in {loc["id"] for loc in default_list}

    full_list = (await client.get("/api/v1/locais?include_inactive=true", headers=auth_headers_a)).json()
    assert local["id"] in {loc["id"] for loc in full_list}


async def test_update_local_renames_without_touching_is_active(client, auth_headers_a):
    create_resp = await client.post("/api/v1/locais", json={"nome": "Recepcao"}, headers=auth_headers_a)
    local_id = create_resp.json()["id"]

    rename_resp = await client.patch(f"/api/v1/locais/{local_id}", json={"nome": "Recepção Central"}, headers=auth_headers_a)
    assert rename_resp.status_code == 200
    updated = rename_resp.json()
    assert updated["nome"] == "Recepção Central"
    assert updated["is_active"] is True


async def test_update_nonexistent_local_returns_404(client, auth_headers_a):
    resp = await client.patch(
        "/api/v1/locais/00000000-0000-0000-0000-000000000000", json={"nome": "X"}, headers=auth_headers_a
    )
    assert resp.status_code == 404
