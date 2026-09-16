"""
tests/integration/test_appointment_satisfaction.py

"Mapa de Dados Insighta" — Domínio Pós-atendimento (Onda 2), pilar
Satisfação/NPS: cobre o fluxo completo — gerar o link autenticado
(recepção), consultar/validar o token publicamente (sem autenticação,
como o paciente faria), submeter a nota e ver o efeito colateral tanto
no agendamento (visit_satisfaction_score) quanto no token (uso único).
"""
import re
from datetime import datetime, timedelta, timezone


async def _create_completed_appointment(client, headers) -> tuple[str, str]:
    """Devolve (appointment_id, patient_id)."""
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Satisfação"}, headers=headers)
    assert patient_resp.status_code == 201, patient_resp.text
    patient_id = patient_resp.json()["id"]

    create_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        },
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    appointment_id = create_resp.json()["id"]

    patch_resp = await client.patch(f"/api/v1/appointments/{appointment_id}", json={"status": "completed"}, headers=headers)
    assert patch_resp.status_code == 200, patch_resp.text
    return appointment_id, patient_id


def _extract_token(url: str) -> str:
    match = re.search(r"/satisfacao/([\w-]+)$", url)
    assert match, f"URL sem token reconhecível: {url}"
    return match.group(1)


async def test_generate_satisfaction_link_requires_completed_status(client, auth_headers_a):
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Agendado"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    create_resp = await client.post(
        "/api/v1/appointments",
        json={"patient_id": patient_id, "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
        headers=auth_headers_a,
    )
    appointment_id = create_resp.json()["id"]

    response = await client.post(f"/api/v1/appointments/{appointment_id}/satisfaction-link", headers=auth_headers_a)
    assert response.status_code == 422


async def test_generate_satisfaction_link_for_nonexistent_appointment_returns_404(client, auth_headers_a):
    import uuid

    response = await client.post(f"/api/v1/appointments/{uuid.uuid4()}/satisfaction-link", headers=auth_headers_a)
    assert response.status_code == 404


async def test_generate_satisfaction_link_for_completed_appointment(client, auth_headers_a):
    appointment_id, _patient_id = await _create_completed_appointment(client, auth_headers_a)

    response = await client.post(f"/api/v1/appointments/{appointment_id}/satisfaction-link", headers=auth_headers_a)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "/satisfacao/" in body["url"]
    assert body["expires_at"]


async def test_public_status_valid_for_fresh_token_and_invalid_for_unknown_token(client, auth_headers_a):
    appointment_id, _patient_id = await _create_completed_appointment(client, auth_headers_a)
    link_resp = await client.post(f"/api/v1/appointments/{appointment_id}/satisfaction-link", headers=auth_headers_a)
    token = _extract_token(link_resp.json()["url"])

    valid_resp = await client.get(f"/api/v1/public/satisfaction/{token}")
    assert valid_resp.status_code == 200
    assert valid_resp.json() == {"valid": True}

    invalid_resp = await client.get("/api/v1/public/satisfaction/token-que-nunca-existiu")
    assert invalid_resp.status_code == 200
    assert invalid_resp.json() == {"valid": False}


async def test_submit_satisfaction_score_updates_appointment_and_consumes_token(client, auth_headers_a):
    appointment_id, patient_id = await _create_completed_appointment(client, auth_headers_a)
    link_resp = await client.post(f"/api/v1/appointments/{appointment_id}/satisfaction-link", headers=auth_headers_a)
    token = _extract_token(link_resp.json()["url"])

    submit_resp = await client.post(f"/api/v1/public/satisfaction/{token}", json={"score": 5})
    assert submit_resp.status_code == 204

    # Token de uso único: depois de submetido, o link some (valid=False).
    status_resp = await client.get(f"/api/v1/public/satisfaction/{token}")
    assert status_resp.json() == {"valid": False}

    # A nota realmente foi gravada no agendamento.
    appointments_resp = await client.get(f"/api/v1/appointments/by-patient/{patient_id}", headers=auth_headers_a)
    appointment = next(a for a in appointments_resp.json() if a["id"] == appointment_id)
    assert appointment["visit_satisfaction_score"] == 5


async def test_submit_satisfaction_score_rejects_out_of_range(client, auth_headers_a):
    appointment_id, _patient_id = await _create_completed_appointment(client, auth_headers_a)
    link_resp = await client.post(f"/api/v1/appointments/{appointment_id}/satisfaction-link", headers=auth_headers_a)
    token = _extract_token(link_resp.json()["url"])

    response = await client.post(f"/api/v1/public/satisfaction/{token}", json={"score": 6})
    assert response.status_code == 422


async def test_submit_satisfaction_score_rejects_reused_token(client, auth_headers_a):
    appointment_id, _patient_id = await _create_completed_appointment(client, auth_headers_a)
    link_resp = await client.post(f"/api/v1/appointments/{appointment_id}/satisfaction-link", headers=auth_headers_a)
    token = _extract_token(link_resp.json()["url"])

    first = await client.post(f"/api/v1/public/satisfaction/{token}", json={"score": 4})
    assert first.status_code == 204

    second = await client.post(f"/api/v1/public/satisfaction/{token}", json={"score": 2})
    assert second.status_code == 400


async def test_submit_satisfaction_score_rejects_unknown_token(client):
    response = await client.post("/api/v1/public/satisfaction/token-que-nunca-existiu", json={"score": 3})
    assert response.status_code == 400
