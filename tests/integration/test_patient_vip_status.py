"""
tests/integration/test_patient_vip_status.py

"Equilíbrio Insighta" (Balanced Scorecard, perna Cliente, mecanismo 1) —
score de paciente de alto valor ("VIP") ponta a ponta via
GET /patients (a MESMA listagem que alimenta o seletor de paciente da
tela de Agenda, ver DECISÃO em PatientService.list_patients_paginated).
O motor puro (regra de VIP) já é coberto sem banco em
tests/test_patient_value_engine.py.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.services.patient_value_engine import VIP_MIN_VISITS


async def _create_patient(client, auth_headers, **overrides) -> dict:
    payload = {"full_name": "Paciente VIP", **overrides}
    resp = await client.post("/api/v1/patients", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _seed_appointments(admin_engine, tenant_id, patient_id, count, *, status="completed"):
    now = datetime.now(timezone.utc) - timedelta(days=1)
    async with admin_engine.begin() as conn:
        for _ in range(count):
            await conn.execute(
                text("INSERT INTO core.appointments (tenant_id, patient_id, scheduled_at, status) VALUES (:t, :p, :dt, :s)"),
                {"t": tenant_id, "p": patient_id, "dt": now, "s": status},
            )


async def _get_patient(client, auth_headers, patient_id: str) -> dict:
    resp = await client.get("/api/v1/patients?limit=200&offset=0", headers=auth_headers)
    assert resp.status_code == 200
    return next(item for item in resp.json()["items"] if item["id"] == patient_id)


async def test_new_patient_is_not_vip_by_default(client, auth_headers_a):
    patient = await _create_patient(client, auth_headers_a)
    listed = await _get_patient(client, auth_headers_a, patient["id"])
    assert listed["is_vip"] is False
    assert listed["vip_reasons"] == []


async def test_frequent_patient_becomes_vip(client, auth_headers_a, admin_engine, tenant_a):
    patient = await _create_patient(client, auth_headers_a, full_name="Paciente Frequente")
    await _seed_appointments(admin_engine, tenant_a, patient["id"], VIP_MIN_VISITS)

    listed = await _get_patient(client, auth_headers_a, patient["id"])
    assert listed["is_vip"] is True
    assert "frequente" in listed["vip_reasons"]


async def test_referring_patient_becomes_vip_even_with_no_visits(client, auth_headers_a):
    referrer = await _create_patient(client, auth_headers_a, full_name="Paciente Indicador")
    await _create_patient(client, auth_headers_a, full_name="Paciente Indicado", referred_by_patient_id=referrer["id"])

    listed = await _get_patient(client, auth_headers_a, referrer["id"])
    assert listed["is_vip"] is True
    assert listed["vip_reasons"] == ["indicou outros pacientes"]


async def test_cancelled_appointments_do_not_count_toward_vip_frequency(client, auth_headers_a, admin_engine, tenant_a):
    patient = await _create_patient(client, auth_headers_a, full_name="Paciente Cancelamentos")
    await _seed_appointments(admin_engine, tenant_a, patient["id"], VIP_MIN_VISITS, status="cancelled")

    listed = await _get_patient(client, auth_headers_a, patient["id"])
    assert listed["is_vip"] is False


async def test_vip_status_is_isolated_between_tenants(client, auth_headers_a, auth_headers_b, admin_engine, tenant_a):
    patient = await _create_patient(client, auth_headers_a, full_name="Paciente Tenant A")
    await _seed_appointments(admin_engine, tenant_a, patient["id"], VIP_MIN_VISITS)

    resp_b = await client.get("/api/v1/patients?limit=200&offset=0", headers=auth_headers_b)
    assert resp_b.status_code == 200
    assert all(item["id"] != patient["id"] for item in resp_b.json()["items"])
