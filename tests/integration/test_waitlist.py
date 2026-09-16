"""
tests/integration/test_waitlist.py

Onda 5 do Plano de Ação, item 16 ("agenda avançada") — lista de espera
de verdade, ver DECISÃO completa em app/sql/057_waitlist_entries.sql.
"""
from datetime import datetime, timedelta, timezone


async def _create_patient(client, headers, full_name="Paciente Espera") -> str:
    resp = await client.post("/api/v1/patients", json={"full_name": full_name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_create_waitlist_entry_and_list_it_back(client, auth_headers_a):
    patient_id = await _create_patient(client, auth_headers_a)

    create_resp = await client.post(
        "/api/v1/waitlist",
        json={"patient_id": patient_id, "preferred_time_window": "manha", "notes": "Só de manhã, tem outro filho pra levar na escola"},
        headers=auth_headers_a,
    )
    assert create_resp.status_code == 201, create_resp.text
    body = create_resp.json()
    assert body["patient_id"] == patient_id
    assert body["patient_full_name"] == "Paciente Espera"
    assert body["preferred_time_window"] == "manha"
    assert body["status"] == "aguardando"
    assert body["professional_id"] is None
    assert body["professional_full_name"] is None

    list_resp = await client.get("/api/v1/waitlist", headers=auth_headers_a)
    assert list_resp.status_code == 200
    listed = list_resp.json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == body["id"]


async def test_create_waitlist_entry_with_professional_resolves_name(client, auth_headers_a, admin_engine, tenant_a):
    from sqlalchemy import text

    patient_id = await _create_patient(client, auth_headers_a)
    professional_id = "11111111-1111-1111-1111-111111111111"
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.professionals (id, tenant_id, full_name) VALUES (:id, :t, 'Dra. Espera')"),
            {"id": professional_id, "t": tenant_a},
        )

    create_resp = await client.post(
        "/api/v1/waitlist", json={"patient_id": patient_id, "professional_id": professional_id}, headers=auth_headers_a
    )
    assert create_resp.status_code == 201, create_resp.text
    assert create_resp.json()["professional_full_name"] == "Dra. Espera"


async def test_create_waitlist_entry_for_unknown_patient_returns_404(client, auth_headers_a):
    response = await client.post(
        "/api/v1/waitlist", json={"patient_id": "00000000-0000-0000-0000-000000000000"}, headers=auth_headers_a
    )
    assert response.status_code == 404


async def test_resolve_waitlist_entry_marks_as_scheduled(client, auth_headers_a):
    patient_id = await _create_patient(client, auth_headers_a)
    entry_resp = await client.post("/api/v1/waitlist", json={"patient_id": patient_id}, headers=auth_headers_a)
    entry_id = entry_resp.json()["id"]

    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={"patient_id": patient_id, "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]

    resolve_resp = await client.post(
        f"/api/v1/waitlist/{entry_id}/resolve", json={"appointment_id": appointment_id}, headers=auth_headers_a
    )
    assert resolve_resp.status_code == 200, resolve_resp.text
    resolved = resolve_resp.json()
    assert resolved["status"] == "agendado"
    assert resolved["resolved_appointment_id"] == appointment_id
    assert resolved["resolved_at"] is not None


async def test_cancel_waitlist_entry(client, auth_headers_a):
    patient_id = await _create_patient(client, auth_headers_a)
    entry_resp = await client.post("/api/v1/waitlist", json={"patient_id": patient_id}, headers=auth_headers_a)
    entry_id = entry_resp.json()["id"]

    cancel_resp = await client.post(f"/api/v1/waitlist/{entry_id}/cancel", headers=auth_headers_a)
    assert cancel_resp.status_code == 200, cancel_resp.text
    assert cancel_resp.json()["status"] == "cancelado"


async def test_resolving_an_already_resolved_entry_returns_409(client, auth_headers_a):
    patient_id = await _create_patient(client, auth_headers_a)
    entry_resp = await client.post("/api/v1/waitlist", json={"patient_id": patient_id}, headers=auth_headers_a)
    entry_id = entry_resp.json()["id"]
    await client.post(f"/api/v1/waitlist/{entry_id}/cancel", headers=auth_headers_a)

    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={"patient_id": patient_id, "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
        headers=auth_headers_a,
    )
    resolve_resp = await client.post(
        f"/api/v1/waitlist/{entry_id}/resolve",
        json={"appointment_id": appointment_resp.json()["id"]},
        headers=auth_headers_a,
    )
    assert resolve_resp.status_code == 409


async def test_list_waitlist_filters_by_status(client, auth_headers_a):
    patient_id = await _create_patient(client, auth_headers_a)
    waiting_entry = await client.post("/api/v1/waitlist", json={"patient_id": patient_id}, headers=auth_headers_a)
    cancelled_entry = await client.post("/api/v1/waitlist", json={"patient_id": patient_id}, headers=auth_headers_a)
    await client.post(f"/api/v1/waitlist/{cancelled_entry.json()['id']}/cancel", headers=auth_headers_a)

    response = await client.get("/api/v1/waitlist?status=aguardando", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == waiting_entry.json()["id"]


async def test_waitlist_isolates_between_tenants(client, auth_headers_a, auth_headers_b):
    patient_id = await _create_patient(client, auth_headers_a)
    await client.post("/api/v1/waitlist", json={"patient_id": patient_id}, headers=auth_headers_a)

    response_b = await client.get("/api/v1/waitlist", headers=auth_headers_b)
    assert response_b.status_code == 200
    assert response_b.json()["total"] == 0


async def test_atendimento_can_create_and_list_waitlist(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao-waitlist@clinica-a.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    patient_id = await _create_patient(client, headers)
    create_resp = await client.post("/api/v1/waitlist", json={"patient_id": patient_id}, headers=headers)
    assert create_resp.status_code == 201

    list_resp = await client.get("/api/v1/waitlist", headers=headers)
    assert list_resp.status_code == 200
