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
