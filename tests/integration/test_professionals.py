"""tests/integration/test_professionals.py — caminho feliz dedicado (RBAC já coberto em test_rbac.py)."""


async def test_create_professional_with_availability_and_list(client, auth_headers_a):
    create_resp = await client.post(
        "/api/v1/professionals",
        json={
            "full_name": "Dr. João",
            "professional_registry": "CRM-12345",
            "specialty": "Clínico Geral",
            "availability": [
                {"weekday": 1, "start_time": "08:00:00", "end_time": "12:00:00"},
                {"weekday": 1, "start_time": "14:00:00", "end_time": "18:00:00"},
            ],
        },
        headers=auth_headers_a,
    )
    assert create_resp.status_code == 201
    body = create_resp.json()
    assert body["full_name"] == "Dr. João"
    assert len(body["availability"]) == 2

    list_resp = await client.get("/api/v1/professionals", headers=auth_headers_a)
    assert list_resp.status_code == 200
    assert any(p["full_name"] == "Dr. João" for p in list_resp.json())


async def test_availability_block_with_end_before_start_is_rejected(client, auth_headers_a):
    response = await client.post(
        "/api/v1/professionals",
        json={
            "full_name": "Dr. Inválido",
            "availability": [{"weekday": 1, "start_time": "18:00:00", "end_time": "08:00:00"}],
        },
        headers=auth_headers_a,
    )
    assert response.status_code == 422


async def test_update_professional_replaces_availability_and_edits_fields(client, auth_headers_a):
    create_resp = await client.post(
        "/api/v1/professionals",
        json={
            "full_name": "Dr. Editável",
            "specialty": "Clínico Geral",
            "availability": [{"weekday": 1, "start_time": "08:00:00", "end_time": "12:00:00"}],
        },
        headers=auth_headers_a,
    )
    professional_id = create_resp.json()["id"]

    update_resp = await client.patch(
        f"/api/v1/professionals/{professional_id}",
        json={
            "specialty": "Cardiologia",
            "availability": [
                {"weekday": 2, "start_time": "09:00:00", "end_time": "11:00:00"},
                {"weekday": 4, "start_time": "13:00:00", "end_time": "17:00:00"},
            ],
        },
        headers=auth_headers_a,
    )
    assert update_resp.status_code == 200
    body = update_resp.json()
    assert body["full_name"] == "Dr. Editável"  # não enviado no PATCH — permanece
    assert body["specialty"] == "Cardiologia"
    assert len(body["availability"]) == 2
    assert {b["weekday"] for b in body["availability"]} == {2, 4}


async def test_update_professional_without_availability_field_keeps_existing_grade(client, auth_headers_a):
    create_resp = await client.post(
        "/api/v1/professionals",
        json={
            "full_name": "Dr. Grade Preservada",
            "availability": [{"weekday": 3, "start_time": "08:00:00", "end_time": "12:00:00"}],
        },
        headers=auth_headers_a,
    )
    professional_id = create_resp.json()["id"]

    update_resp = await client.patch(
        f"/api/v1/professionals/{professional_id}", json={"full_name": "Dr. Nome Novo"}, headers=auth_headers_a
    )
    assert update_resp.status_code == 200
    body = update_resp.json()
    assert body["full_name"] == "Dr. Nome Novo"
    assert len(body["availability"]) == 1
    assert body["availability"][0]["weekday"] == 3


async def test_deactivated_professional_excluded_from_default_list_but_included_with_include_inactive(client, auth_headers_a):
    create_resp = await client.post("/api/v1/professionals", json={"full_name": "Dr. Vai Sair"}, headers=auth_headers_a)
    professional_id = create_resp.json()["id"]

    deactivate_resp = await client.patch(
        f"/api/v1/professionals/{professional_id}", json={"is_active": False}, headers=auth_headers_a
    )
    assert deactivate_resp.status_code == 200
    assert deactivate_resp.json()["is_active"] is False

    default_list = await client.get("/api/v1/professionals", headers=auth_headers_a)
    assert not any(p["id"] == professional_id for p in default_list.json())

    full_list = await client.get("/api/v1/professionals?include_inactive=true", headers=auth_headers_a)
    assert any(p["id"] == professional_id and p["is_active"] is False for p in full_list.json())


async def test_update_nonexistent_professional_returns_404(client, auth_headers_a):
    import uuid

    response = await client.patch(
        f"/api/v1/professionals/{uuid.uuid4()}", json={"full_name": "Ninguém"}, headers=auth_headers_a
    )
    assert response.status_code == 404
