"""
tests/integration/test_patient_outreach_log.py

Onda 4 do Plano de Ação, item 12 ("CRM de verdade: ação, não só
leitura") — registro de contato de reativação e anotação nas listas de
reativação (InactivePatients, RFM action_items). Ver DECISÃO completa
em app/sql/055_patient_outreach_log.sql.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text


async def _create_appointment(admin_engine, tenant_id: str, patient_id: str, scheduled_at: datetime) -> None:
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.appointments (tenant_id, patient_id, scheduled_at, status) "
                "VALUES (:t, :p, :dt, 'completed')"
            ),
            {"t": tenant_id, "p": patient_id, "dt": scheduled_at},
        )


async def test_create_outreach_log_and_list_it_back(client, auth_headers_a):
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Contato"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    create_resp = await client.post(
        f"/api/v1/patients/{patient_id}/outreach-log",
        json={"channel": "whatsapp", "outcome": "agendou", "notes": "Reagendou pra semana que vem"},
        headers=auth_headers_a,
    )
    assert create_resp.status_code == 201, create_resp.text
    body = create_resp.json()
    assert body["patient_id"] == patient_id
    assert body["channel"] == "whatsapp"
    assert body["outcome"] == "agendou"
    assert body["notes"] == "Reagendou pra semana que vem"

    list_resp = await client.get(f"/api/v1/patients/{patient_id}/outreach-log", headers=auth_headers_a)
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert len(items) == 1
    assert items[0]["id"] == body["id"]


async def test_outreach_log_orders_most_recent_first(client, auth_headers_a):
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Vários Contatos"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    await client.post(
        f"/api/v1/patients/{patient_id}/outreach-log",
        json={"channel": "telefone", "outcome": "sem_resposta"},
        headers=auth_headers_a,
    )
    await client.post(
        f"/api/v1/patients/{patient_id}/outreach-log",
        json={"channel": "sms", "outcome": "contatado"},
        headers=auth_headers_a,
    )

    list_resp = await client.get(f"/api/v1/patients/{patient_id}/outreach-log", headers=auth_headers_a)
    items = list_resp.json()
    assert len(items) == 2
    assert items[0]["outcome"] == "contatado"  # o mais recente dos dois


async def test_create_outreach_log_for_unknown_patient_returns_404(client, auth_headers_a):
    response = await client.post(
        "/api/v1/patients/00000000-0000-0000-0000-000000000000/outreach-log",
        json={"channel": "telefone", "outcome": "contatado"},
        headers=auth_headers_a,
    )
    assert response.status_code == 404


async def test_outreach_log_rejects_unknown_channel(client, auth_headers_a):
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Canal Inválido"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    response = await client.post(
        f"/api/v1/patients/{patient_id}/outreach-log",
        json={"channel": "pombo-correio", "outcome": "contatado"},
        headers=auth_headers_a,
    )
    assert response.status_code == 422


async def test_outreach_log_isolates_between_tenants(client, auth_headers_a, auth_headers_b):
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Isolamento"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    await client.post(
        f"/api/v1/patients/{patient_id}/outreach-log",
        json={"channel": "telefone", "outcome": "contatado"},
        headers=auth_headers_a,
    )

    # Tenant B nem enxerga o paciente do tenant A (RLS já bloqueia o
    # próprio Patient), então tentar logar contato nele já cai em 404.
    response_b = await client.post(
        f"/api/v1/patients/{patient_id}/outreach-log",
        json={"channel": "telefone", "outcome": "contatado"},
        headers=auth_headers_b,
    )
    assert response_b.status_code == 404


async def test_inactive_patients_annotates_last_outreach(client, auth_headers_a, admin_engine, tenant_a):
    """Onda 4, item 12 — a carteira de inativos mostra quem já foi
    contatado, sem precisar de uma tela separada pra cruzar as duas
    listas."""
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Sumido Contatado"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    await _create_appointment(admin_engine, tenant_a, patient_id, datetime.now(timezone.utc) - timedelta(days=400))

    inactive_before = await client.get("/api/v1/analytics/inactive-patients", headers=auth_headers_a)
    assert inactive_before.json()["items"][0]["last_outreach_at"] is None
    assert inactive_before.json()["items"][0]["last_outreach_outcome"] is None

    await client.post(
        f"/api/v1/patients/{patient_id}/outreach-log",
        json={"channel": "whatsapp", "outcome": "sem_resposta"},
        headers=auth_headers_a,
    )

    inactive_after = await client.get("/api/v1/analytics/inactive-patients", headers=auth_headers_a)
    item = inactive_after.json()["items"][0]
    assert item["last_outreach_at"] is not None
    assert item["last_outreach_outcome"] == "sem_resposta"
