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


# --- Fase 4: Local de Atendimento + Tipo de Paciente ---


async def test_create_appointment_with_local_and_tipo_paciente(client, auth_headers_a):
    local_resp = await client.post("/api/v1/locais", json={"nome": "Pronto Socorro Adulto"}, headers=auth_headers_a)
    local_id = local_resp.json()["id"]

    patient_id = await _create_patient(client, auth_headers_a)
    appointment = await _create_appointment(
        client, auth_headers_a, patient_id, local_id=local_id, tipo_paciente="pronto_socorro"
    )
    assert appointment["local_id"] == local_id
    assert appointment["tipo_paciente"] == "pronto_socorro"


async def test_create_appointment_rejects_unknown_tipo_paciente(client, auth_headers_a):
    patient_id = await _create_patient(client, auth_headers_a)
    payload = {
        "patient_id": patient_id,
        "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "tipo_paciente": "internado",
    }
    resp = await client.post("/api/v1/appointments", json=payload, headers=auth_headers_a)
    assert resp.status_code == 422


async def test_create_appointment_rejects_unknown_local(client, auth_headers_a):
    patient_id = await _create_patient(client, auth_headers_a)
    payload = {
        "patient_id": patient_id,
        "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "local_id": "00000000-0000-0000-0000-000000000000",
    }
    resp = await client.post("/api/v1/appointments", json=payload, headers=auth_headers_a)
    assert resp.status_code == 404


async def test_patch_fills_in_local_and_tipo_paciente_later(client, auth_headers_a):
    """Caso real: o tipo de atendimento/local só é confirmado durante o
    Atendimento, não necessariamente no Agendamento."""
    patient_id = await _create_patient(client, auth_headers_a)
    appointment = await _create_appointment(client, auth_headers_a, patient_id)
    assert appointment["local_id"] is None
    assert appointment["tipo_paciente"] is None

    local_resp = await client.post("/api/v1/locais", json={"nome": "Recepção Central"}, headers=auth_headers_a)
    local_id = local_resp.json()["id"]

    patch_resp = await client.patch(
        f"/api/v1/appointments/{appointment['id']}",
        json={"local_id": local_id, "tipo_paciente": "ambulatorial"},
        headers=auth_headers_a,
    )
    assert patch_resp.status_code == 200, patch_resp.text
    updated = patch_resp.json()
    assert updated["local_id"] == local_id
    assert updated["tipo_paciente"] == "ambulatorial"


# --- GET /appointments (Achado 12 / tela de listagem por período) ---
#
# Peça que faltava depois do Achado 12 da Auditoria de Templates e
# Insights: os insights de canal de agendamento/motivo de cancelamento
# (ver smart_insights_engine.py) apontavam o problema em AGREGADO, mas
# não existia nenhuma tela que listasse agendamentos individuais de um
# período com esses campos. Os testes abaixo cobrem o que
# test_smart_insights_flags_booking_channel_no_show_from_real_data (em
# test_analytics.py) NÃO cobre: o endpoint de listagem em si — filtro de
# período, paginação (total/limit/offset) e o JOIN com Patient para
# resolver patient_name sem N+1 no frontend.


async def test_list_appointments_filters_by_date_range_and_resolves_patient_name(client, auth_headers_a, admin_engine, tenant_a):
    from tests.integration.test_analytics import _create_appointment_direct

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Período"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    inside = datetime(2026, 3, 15, 10, 0, tzinfo=timezone.utc)
    before_window = datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc)
    after_window = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)

    await _create_appointment_direct(
        admin_engine,
        tenant_a,
        patient_id,
        inside,
        status="no_show",
        booking_channel="whatsapp",
        cancellation_reason=None,
    )
    await _create_appointment_direct(admin_engine, tenant_a, patient_id, before_window, status="completed")
    await _create_appointment_direct(admin_engine, tenant_a, patient_id, after_window, status="completed")

    response = await client.get(
        "/api/v1/appointments?date_from=2026-03-01&date_to=2026-03-31", headers=auth_headers_a
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["patient_name"] == "Paciente Período"
    assert item["status"] == "no_show"
    assert item["booking_channel"] == "whatsapp"
    assert item["cancellation_reason"] is None


async def test_list_appointments_paginates_with_total_limit_offset(client, auth_headers_a, admin_engine, tenant_a):
    from tests.integration.test_analytics import _create_appointment_direct

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Paginação"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    for day in range(1, 6):
        await _create_appointment_direct(
            admin_engine,
            tenant_a,
            patient_id,
            datetime(2026, 5, day, 10, 0, tzinfo=timezone.utc),
            status="scheduled",
        )

    response = await client.get(
        "/api/v1/appointments?date_from=2026-05-01&date_to=2026-05-31&limit=2&offset=0", headers=auth_headers_a
    )
    assert response.status_code == 200, response.text
    first_page = response.json()
    assert first_page["total"] == 5
    assert first_page["limit"] == 2
    assert first_page["offset"] == 0
    assert len(first_page["items"]) == 2
    # scheduled_at.desc() — o mais recente primeiro (dia 5).
    assert first_page["items"][0]["scheduled_at"].startswith("2026-05-05")

    response = await client.get(
        "/api/v1/appointments?date_from=2026-05-01&date_to=2026-05-31&limit=2&offset=4", headers=auth_headers_a
    )
    last_page = response.json()
    assert last_page["total"] == 5
    assert len(last_page["items"]) == 1
    assert last_page["items"][0]["scheduled_at"].startswith("2026-05-01")


async def test_list_appointments_empty_range_returns_empty_page(client, auth_headers_a):
    response = await client.get(
        "/api/v1/appointments?date_from=2026-01-01&date_to=2026-01-31", headers=auth_headers_a
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []


async def test_list_appointments_requires_authentication(client):
    response = await client.get("/api/v1/appointments?date_from=2026-01-01&date_to=2026-01-31")
    assert response.status_code == 401
