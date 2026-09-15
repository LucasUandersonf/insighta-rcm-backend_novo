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


# ---------------------------------------------------------------------
# "Mapa de Dados Insighta" — Domínio Profissional (Onda 1): ausência
# futura planejada (férias, licença) — diferente da grade semanal, que
# é recorrente.
# ---------------------------------------------------------------------


async def _create_professional(client, headers, full_name="Dr. Ausência") -> str:
    resp = await client.post("/api/v1/professionals", json={"full_name": full_name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_new_professional_has_no_planned_absences(client, auth_headers_a):
    professional_id = await _create_professional(client, auth_headers_a)
    list_resp = await client.get("/api/v1/professionals", headers=auth_headers_a)
    professional = next(p for p in list_resp.json() if p["id"] == professional_id)
    assert professional["planned_absences"] == []


async def test_add_planned_absence_and_see_it_in_professional_list(client, auth_headers_a):
    professional_id = await _create_professional(client, auth_headers_a)

    add_resp = await client.post(
        f"/api/v1/professionals/{professional_id}/planned-absences",
        json={"start_date": "2026-12-20", "end_date": "2027-01-05", "reason": "Férias de fim de ano"},
        headers=auth_headers_a,
    )
    assert add_resp.status_code == 201, add_resp.text
    absence = add_resp.json()
    assert absence["start_date"] == "2026-12-20"
    assert absence["end_date"] == "2027-01-05"
    assert absence["reason"] == "Férias de fim de ano"

    list_resp = await client.get("/api/v1/professionals", headers=auth_headers_a)
    professional = next(p for p in list_resp.json() if p["id"] == professional_id)
    assert len(professional["planned_absences"]) == 1
    assert professional["planned_absences"][0]["id"] == absence["id"]


async def test_add_planned_absence_rejects_end_before_start(client, auth_headers_a):
    professional_id = await _create_professional(client, auth_headers_a)
    response = await client.post(
        f"/api/v1/professionals/{professional_id}/planned-absences",
        json={"start_date": "2026-12-20", "end_date": "2026-12-01"},
        headers=auth_headers_a,
    )
    assert response.status_code == 422


async def test_add_planned_absence_404_for_unknown_professional(client, auth_headers_a):
    import uuid

    response = await client.post(
        f"/api/v1/professionals/{uuid.uuid4()}/planned-absences",
        json={"start_date": "2026-12-20", "end_date": "2026-12-25"},
        headers=auth_headers_a,
    )
    assert response.status_code == 404


async def test_multiple_planned_absences_coexist(client, auth_headers_a):
    """Lançar uma nova ausência nunca apaga a anterior — diferente da
    grade semanal, que É substituída por inteiro a cada PATCH."""
    professional_id = await _create_professional(client, auth_headers_a)
    await client.post(
        f"/api/v1/professionals/{professional_id}/planned-absences",
        json={"start_date": "2026-07-01", "end_date": "2026-07-10"},
        headers=auth_headers_a,
    )
    await client.post(
        f"/api/v1/professionals/{professional_id}/planned-absences",
        json={"start_date": "2026-12-20", "end_date": "2027-01-05"},
        headers=auth_headers_a,
    )

    list_resp = await client.get("/api/v1/professionals", headers=auth_headers_a)
    professional = next(p for p in list_resp.json() if p["id"] == professional_id)
    assert len(professional["planned_absences"]) == 2


async def test_remove_planned_absence(client, auth_headers_a):
    professional_id = await _create_professional(client, auth_headers_a)
    add_resp = await client.post(
        f"/api/v1/professionals/{professional_id}/planned-absences",
        json={"start_date": "2026-12-20", "end_date": "2027-01-05"},
        headers=auth_headers_a,
    )
    absence_id = add_resp.json()["id"]

    delete_resp = await client.delete(
        f"/api/v1/professionals/{professional_id}/planned-absences/{absence_id}", headers=auth_headers_a
    )
    assert delete_resp.status_code == 204

    list_resp = await client.get("/api/v1/professionals", headers=auth_headers_a)
    professional = next(p for p in list_resp.json() if p["id"] == professional_id)
    assert professional["planned_absences"] == []


async def test_remove_unknown_planned_absence_returns_404(client, auth_headers_a):
    import uuid

    professional_id = await _create_professional(client, auth_headers_a)
    response = await client.delete(
        f"/api/v1/professionals/{professional_id}/planned-absences/{uuid.uuid4()}", headers=auth_headers_a
    )
    assert response.status_code == 404


async def test_atendimento_cannot_add_planned_absence(client, admin_engine, tenant_a, auth_headers_a):
    from tests.conftest import _insert_user, _login

    professional_id = await _create_professional(client, auth_headers_a)
    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao@planned-absence.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.post(
        f"/api/v1/professionals/{professional_id}/planned-absences",
        json={"start_date": "2026-12-20", "end_date": "2027-01-05"},
        headers=headers,
    )
    assert response.status_code == 403
