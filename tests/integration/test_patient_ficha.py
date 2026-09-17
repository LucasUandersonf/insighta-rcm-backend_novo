"""
tests/integration/test_patient_ficha.py

Ficha do Paciente (Roadmap "Rumo à Nota 9", Fase 4) — pedido direto do
usuário: "podemos juntar dados de pessoa física, com os dados de
agendamento, com os dados de atendimento... cada conta possui um
registro depois da abertura de atendimento, não seria legal termos
isto". Ponta a ponta via HTTP: cria paciente -> agendamento -> fatura, e
confirma que a ficha junta os três.
"""
import uuid
from datetime import datetime, timedelta, timezone

from tests.integration.test_billing_denial_engine import _create_insurance_plan


async def test_search_patients_by_name_and_cpf(client, auth_headers_a):
    await client.post("/api/v1/patients", json={"full_name": "Maria da Silva", "cpf": "12345678900"}, headers=auth_headers_a)
    await client.post("/api/v1/patients", json={"full_name": "João Pereira"}, headers=auth_headers_a)

    by_name = await client.get("/api/v1/patients/search?q=Maria", headers=auth_headers_a)
    assert by_name.status_code == 200
    assert [p["full_name"] for p in by_name.json()] == ["Maria da Silva"]

    by_cpf = await client.get("/api/v1/patients/search?q=123.456.789-00", headers=auth_headers_a)
    assert [p["full_name"] for p in by_cpf.json()] == ["Maria da Silva"]

    too_short = await client.get("/api/v1/patients/search?q=M", headers=auth_headers_a)
    assert too_short.status_code == 422  # min_length=2, mesmo padrão de /billing/search


async def test_ficha_joins_person_appointment_and_billing(client, auth_headers_a, admin_engine, tenant_a):
    patient = (
        await client.post("/api/v1/patients", json={"full_name": "Carlos Andrade", "cpf": "98765432100"}, headers=auth_headers_a)
    ).json()
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)

    appt = (
        await client.post(
            "/api/v1/appointments",
            json={
                "patient_id": patient["id"],
                "insurance_plan_id": plan_id,
                "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                "procedure_code": "10101012",
                "cid_code": "J06",
            },
            headers=auth_headers_a,
        )
    ).json()
    billing = (
        await client.post(
            "/api/v1/billing",
            json={"appointment_id": appt["id"], "insurance_plan_id": plan_id, "charged_value": 200.0},
            headers=auth_headers_a,
        )
    ).json()

    response = await client.get(f"/api/v1/patients/{patient['id']}/ficha", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()

    assert body["patient"]["full_name"] == "Carlos Andrade"
    assert body["patient"]["cpf"] == "98765432100"
    assert body["summary"]["total_appointments"] == 1
    assert body["summary"]["total_billed"] == 200.0

    assert len(body["appointments"]) == 1
    appointment_item = body["appointments"][0]
    assert appointment_item["id"] == appt["id"]
    assert appointment_item["status"] == "scheduled"
    assert len(appointment_item["billings"]) == 1
    assert appointment_item["billings"][0]["id"] == billing["id"]
    assert appointment_item["billings"][0]["charged_value"] == 200.0


async def test_ficha_computes_no_show_rate_only_over_resolved_appointments(client, auth_headers_a, admin_engine, tenant_a):
    patient = (await client.post("/api/v1/patients", json={"full_name": "Ana Rocha"}, headers=auth_headers_a)).json()

    from sqlalchemy import text

    async with admin_engine.begin() as conn:
        for status_value in ("no_show", "completed", "completed", "scheduled"):
            await conn.execute(
                text(
                    "INSERT INTO core.appointments (tenant_id, patient_id, scheduled_at, status) "
                    "VALUES (:t, :p, :dt, :status)"
                ),
                {"t": tenant_a, "p": patient["id"], "dt": datetime.now(timezone.utc), "status": status_value},
            )

    response = await client.get(f"/api/v1/patients/{patient['id']}/ficha", headers=auth_headers_a)
    assert response.status_code == 200
    summary = response.json()["summary"]
    assert summary["total_appointments"] == 4
    assert summary["no_show_count"] == 1
    # 1 falta em 3 atendimentos RESOLVIDOS (no_show + completed) — o
    # 'scheduled' ainda em aberto não entra no denominador.
    assert summary["no_show_rate"] == 1 / 3


async def test_ficha_returns_404_for_unknown_patient(client, auth_headers_a):
    response = await client.get(f"/api/v1/patients/{uuid.uuid4()}/ficha", headers=auth_headers_a)
    assert response.status_code == 404
