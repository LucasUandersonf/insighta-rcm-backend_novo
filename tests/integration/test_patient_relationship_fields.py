"""
tests/integration/test_patient_relationship_fields.py

"Mapa de Dados Insighta" — Domínio Paciente (Onda 1): o paciente
RELACIONAL, não só transacional. Ver DECISÃO completa em
045_patient_relationship_fields.sql e app/services/patient_service.py.
"""


async def _create_patient(client, auth_headers, **overrides) -> dict:
    payload = {"full_name": "Paciente Relacional", **overrides}
    resp = await client.post("/api/v1/patients", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_new_patient_relationship_fields_default_to_none(client, auth_headers_a):
    body = await _create_patient(client, auth_headers_a)
    assert body["referred_by_patient_id"] is None
    assert body["communication_consent"] is None
    assert body["preferred_time_window"] is None
    assert body["zip_code"] is None


async def test_create_patient_with_referral_and_consent(client, auth_headers_a):
    referrer = await _create_patient(client, auth_headers_a, full_name="Paciente Indicador")

    body = await _create_patient(
        client,
        auth_headers_a,
        full_name="Paciente Indicado",
        referred_by_patient_id=referrer["id"],
        communication_consent=True,
        preferred_time_window="manha",
        zip_code="01310-100",
    )
    assert body["referred_by_patient_id"] == referrer["id"]
    assert body["communication_consent"] is True
    assert body["preferred_time_window"] == "manha"
    assert body["zip_code"] == "01310100"  # sanitizado, só dígitos


async def test_create_patient_rejects_referrer_from_another_tenant(client, auth_headers_a, auth_headers_b):
    other_tenant_patient = await _create_patient(client, auth_headers_b, full_name="Paciente Tenant B")

    resp = await client.post(
        "/api/v1/patients",
        json={"full_name": "Paciente Tenant A", "referred_by_patient_id": other_tenant_patient["id"]},
        headers=auth_headers_a,
    )
    assert resp.status_code == 422


async def test_create_patient_rejects_invalid_preferred_time_window(client, auth_headers_a):
    resp = await client.post(
        "/api/v1/patients",
        json={"full_name": "Paciente Inválido", "preferred_time_window": "madrugada"},
        headers=auth_headers_a,
    )
    assert resp.status_code == 422


async def test_create_patient_rejects_malformed_zip_code(client, auth_headers_a):
    resp = await client.post(
        "/api/v1/patients", json={"full_name": "Paciente CEP Inválido", "zip_code": "123"}, headers=auth_headers_a
    )
    assert resp.status_code == 422


async def test_update_patient_fills_in_relationship_fields_later(client, auth_headers_a):
    body = await _create_patient(client, auth_headers_a)

    resp = await client.patch(
        f"/api/v1/patients/{body['id']}",
        json={"communication_consent": True, "preferred_time_window": "tarde", "zip_code": "20040-020"},
        headers=auth_headers_a,
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["communication_consent"] is True
    assert updated["preferred_time_window"] == "tarde"
    assert updated["zip_code"] == "20040020"


async def test_update_patient_can_record_explicit_consent_refusal(client, auth_headers_a):
    """False é um estado válido e distinto de None — recusa explícita,
    nunca campanha automatizada pra esse paciente."""
    body = await _create_patient(client, auth_headers_a)

    resp = await client.patch(
        f"/api/v1/patients/{body['id']}", json={"communication_consent": False}, headers=auth_headers_a
    )
    assert resp.status_code == 200
    assert resp.json()["communication_consent"] is False


async def test_update_patient_rejects_self_referral(client, auth_headers_a):
    body = await _create_patient(client, auth_headers_a)

    resp = await client.patch(
        f"/api/v1/patients/{body['id']}", json={"referred_by_patient_id": body["id"]}, headers=auth_headers_a
    )
    assert resp.status_code == 422


async def test_update_patient_404_for_unknown_patient(client, auth_headers_a):
    resp = await client.patch(
        "/api/v1/patients/00000000-0000-0000-0000-000000000000",
        json={"communication_consent": True},
        headers=auth_headers_a,
    )
    assert resp.status_code == 404


async def test_anonymize_patient_clears_zip_code_but_keeps_referral_and_consent(client, auth_headers_a):
    """zip_code é dado de localização, entra na eliminação (LGPD).
    communication_consent/preferred_time_window/referred_by_patient_id
    não identificam o titular sozinhos — preservados (o segundo ainda
    sustenta o histórico de indicação de OUTROS pacientes)."""
    referrer = await _create_patient(client, auth_headers_a, full_name="Indicador Preservado")
    body = await _create_patient(
        client,
        auth_headers_a,
        full_name="Paciente a Anonimizar",
        referred_by_patient_id=referrer["id"],
        communication_consent=True,
        preferred_time_window="noite",
        zip_code="01310-100",
    )

    resp = await client.post(f"/api/v1/patients/{body['id']}/anonymize", headers=auth_headers_a)
    assert resp.status_code == 200
    anonymized = resp.json()
    assert anonymized["zip_code"] is None
    assert anonymized["referred_by_patient_id"] == referrer["id"]
    assert anonymized["communication_consent"] is True
    assert anonymized["preferred_time_window"] == "noite"
