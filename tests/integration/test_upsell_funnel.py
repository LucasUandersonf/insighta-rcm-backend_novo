"""
tests/integration/test_upsell_funnel.py

"Equilíbrio Insighta" (Balanced Scorecard, perna Cliente, mecanismo 3) —
funil de upsell (oferecido × aceito), ponta a ponta via
GET /analytics/upsell-funnel. Prova que
AnalyticsService.get_upsell_funnel agrega o dado real do banco (só
ofertas de fato registradas, aceite explícito != recusa por omissão,
RLS isola entre tenants).
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text


async def _create_patient(client, auth_headers, full_name="Paciente Upsell") -> str:
    resp = await client.post("/api/v1/patients", json={"full_name": full_name}, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _seed_offer(admin_engine, tenant_id, patient_id, *, scheduled_at, procedure, declined):
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.appointments (tenant_id, patient_id, scheduled_at, status, addon_offered_procedure, addon_declined) "
                "VALUES (:t, :p, :dt, 'completed', :proc, :declined)"
            ),
            {"t": tenant_id, "p": patient_id, "dt": scheduled_at, "proc": procedure, "declined": declined},
        )


async def test_no_offers_yet_returns_empty_funnel(client, auth_headers_a):
    response = await client.get("/api/v1/analytics/upsell-funnel", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total_offered"] == 0
    assert body["total_accepted"] == 0
    assert body["overall_acceptance_rate"] is None


async def test_counts_offered_and_accepted_by_procedure(client, auth_headers_a, admin_engine, tenant_a):
    patient_id = await _create_patient(client, auth_headers_a)
    now = datetime.now(timezone.utc) - timedelta(days=1)

    # Clareamento: 2 ofertas, 1 aceita, 1 recusada.
    await _seed_offer(admin_engine, tenant_a, patient_id, scheduled_at=now, procedure="Clareamento dental", declined=False)
    await _seed_offer(admin_engine, tenant_a, patient_id, scheduled_at=now, procedure="Clareamento dental", declined=True)
    # Limpeza avançada: 1 oferta, aceita.
    await _seed_offer(admin_engine, tenant_a, patient_id, scheduled_at=now, procedure="Limpeza avançada", declined=False)

    response = await client.get("/api/v1/analytics/upsell-funnel", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["total_offered"] == 3
    assert body["total_accepted"] == 2
    assert body["overall_acceptance_rate"] == pytest.approx(2 / 3)

    by_name = {item["procedure_name"]: item for item in body["items"]}
    assert by_name["Clareamento dental"]["offered_count"] == 2
    assert by_name["Clareamento dental"]["accepted_count"] == 1
    assert by_name["Clareamento dental"]["acceptance_rate"] == pytest.approx(0.5)
    assert by_name["Limpeza avançada"]["offered_count"] == 1
    assert by_name["Limpeza avançada"]["accepted_count"] == 1
    assert by_name["Limpeza avançada"]["acceptance_rate"] == 1.0


async def test_items_are_sorted_by_offered_count_descending(client, auth_headers_a, admin_engine, tenant_a):
    patient_id = await _create_patient(client, auth_headers_a)
    now = datetime.now(timezone.utc) - timedelta(days=1)

    await _seed_offer(admin_engine, tenant_a, patient_id, scheduled_at=now, procedure="Menos ofertado", declined=False)
    for _ in range(3):
        await _seed_offer(admin_engine, tenant_a, patient_id, scheduled_at=now, procedure="Mais ofertado", declined=False)

    response = await client.get("/api/v1/analytics/upsell-funnel", headers=auth_headers_a)
    assert response.status_code == 200
    names = [item["procedure_name"] for item in response.json()["items"]]
    assert names == ["Mais ofertado", "Menos ofertado"]


async def test_pending_outcome_counts_as_offered_but_never_as_accepted(client, auth_headers_a, admin_engine, tenant_a):
    """`addon_declined IS NULL` — oferta feita, desfecho ainda não
    registrado. Não deveria virar "aceita" por omissão."""
    patient_id = await _create_patient(client, auth_headers_a)
    now = datetime.now(timezone.utc) - timedelta(days=1)
    await _seed_offer(admin_engine, tenant_a, patient_id, scheduled_at=now, procedure="Pendente", declined=None)

    response = await client.get("/api/v1/analytics/upsell-funnel", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["total_offered"] == 1
    assert body["total_accepted"] == 0


async def test_upsell_funnel_is_isolated_between_tenants(client, auth_headers_a, auth_headers_b, admin_engine, tenant_a):
    patient_id = await _create_patient(client, auth_headers_a)
    now = datetime.now(timezone.utc) - timedelta(days=1)
    await _seed_offer(admin_engine, tenant_a, patient_id, scheduled_at=now, procedure="Só do tenant A", declined=False)

    response_b = await client.get("/api/v1/analytics/upsell-funnel", headers=auth_headers_b)
    assert response_b.status_code == 200
    assert response_b.json()["items"] == []
