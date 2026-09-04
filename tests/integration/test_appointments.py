"""
tests/integration/test_appointments.py

Primeira cobertura de teste para app/api/v1/endpoints/appointments.py
(confirmado via busca antes de escrever isto: zero testes existiam).
Cobre especificamente o gap achado analisando o fluxo real Agendamento
-> Atendimento -> Faturamento do mercado (ERPs como o Moderna): antes de
PATCH /appointments/{id} existir, um agendamento nascia "scheduled" e
não tinha NENHUM jeito de virar "completed"/"no_show"/"cancelled" pela
API — só a ingestão em massa de CSV gravava status diferente de
"scheduled", direto no banco.
"""
from datetime import datetime, timedelta, timezone


async def _create_patient(client, headers, full_name="Paciente Teste") -> str:
    response = await client.post("/api/v1/patients", json={"full_name": full_name}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _create_appointment(client, headers, patient_id, **extra) -> dict:
    scheduled_at = extra.pop("scheduled_at", None) or (datetime.now(timezone.utc) + timedelta(days=1))
    payload = {"patient_id": patient_id, "scheduled_at": scheduled_at.isoformat(), **extra}
    response = await client.post("/api/v1/appointments", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_appointment_defaults_to_scheduled(client, auth_headers_a):
    patient_id = await _create_patient(client, auth_headers_a)
    appointment = await _create_appointment(client, auth_headers_a, patient_id)
    assert appointment["status"] == "scheduled"
    assert appointment["procedure_code"] is None
    assert appointment["cid_code"] is None


async def test_patch_marks_appointment_completed_with_procedure_filled_in_later(client, auth_headers_a):
    """
    Caso de uso real: o paciente agenda "consulta" sem saber ainda qual
    vai ser o código TUSS/CID — só depois do atendimento o profissional
    sabe. Cobre exatamente o cenário que motivou procedure_code/cid_code
    virarem editáveis em AppointmentUpdateRequest.
    """
    patient_id = await _create_patient(client, auth_headers_a)
    appointment = await _create_appointment(client, auth_headers_a, patient_id)

    patch_resp = await client.patch(
        f"/api/v1/appointments/{appointment['id']}",
        json={"status": "completed", "procedure_code": "10101012", "cid_code": "J06"},
        headers=auth_headers_a,
    )
    assert patch_resp.status_code == 200, patch_resp.text
    updated = patch_resp.json()
    assert updated["status"] == "completed"
    assert updated["procedure_code"] == "10101012"
    assert updated["cid_code"] == "J06"


async def test_patch_marks_appointment_as_no_show(client, auth_headers_a):
    patient_id = await _create_patient(client, auth_headers_a)
    appointment = await _create_appointment(client, auth_headers_a, patient_id)

    patch_resp = await client.patch(
        f"/api/v1/appointments/{appointment['id']}", json={"status": "no_show"}, headers=auth_headers_a
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["status"] == "no_show"


async def test_patch_rejects_unknown_status(client, auth_headers_a):
    """
    Status é string livre no banco (não um enum de Postgres) — sem essa
    validação, um valor fora do vocabulário conhecido (scheduled/
    completed/no_show/cancelled) some silenciosamente de toda métrica
    de capacidade/ocupação/risco de falta que filtra comparando string.
    """
    patient_id = await _create_patient(client, auth_headers_a)
    appointment = await _create_appointment(client, auth_headers_a, patient_id)

    patch_resp = await client.patch(
        f"/api/v1/appointments/{appointment['id']}", json={"status": "confirmado"}, headers=auth_headers_a
    )
    assert patch_resp.status_code == 422


async def test_patch_nonexistent_appointment_returns_404(client, auth_headers_a):
    resp = await client.patch(
        "/api/v1/appointments/00000000-0000-0000-0000-000000000000",
        json={"status": "cancelled"},
        headers=auth_headers_a,
    )
    assert resp.status_code == 404


async def test_patch_partial_update_does_not_touch_other_fields(client, auth_headers_a):
    """Mesmo padrão de PATCH parcial das demais entidades: só o campo
    enviado muda, o resto permanece intacto."""
    patient_id = await _create_patient(client, auth_headers_a)
    appointment = await _create_appointment(client, auth_headers_a, patient_id, procedure_code="10101012", cid_code="J06")

    patch_resp = await client.patch(
        f"/api/v1/appointments/{appointment['id']}", json={"status": "completed"}, headers=auth_headers_a
    )
    assert patch_resp.status_code == 200
    updated = patch_resp.json()
    assert updated["status"] == "completed"
    assert updated["procedure_code"] == "10101012"  # não mudou
    assert updated["cid_code"] == "J06"  # não mudou
