"""
tests/integration/test_recall_candidates.py

Candidatos a recontato (Sala de Comando) — a lista real por trás dos
botões de ação dos insights de agenda que apontam pra um dia da semana
(_weekday_drop_insight/_weekday_no_show_rate_insight) ou um profissional
específico (_capacity_drop_insight). Prova ponta a ponta que só entra
quem JÁ foi atendido e NÃO tem retorno futuro marcado, que os filtros de
dia da semana/profissional funcionam, que o endpoint exige exatamente um
dos dois, e que o RLS isola entre tenants.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text


async def _create_appointment(
    admin_engine, tenant_id: str, patient_id: str, scheduled_at: datetime, *, status: str = "completed", professional_id: str | None = None
) -> None:
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.appointments (tenant_id, patient_id, professional_id, scheduled_at, status) "
                "VALUES (:t, :p, :prof, :dt, :status)"
            ),
            {"t": tenant_id, "p": patient_id, "prof": professional_id, "dt": scheduled_at, "status": status},
        )


def _last_wednesday_before(now: datetime) -> datetime:
    """Uma quarta-feira (weekday=3 na convenção Python 0=segunda..6=domingo,
    que é o que datetime.weekday() devolve) recente e no PASSADO, pra
    servir de 'último atendimento' determinístico no teste — sem
    depender de qual dia da semana é hoje quando o teste roda."""
    days_back = (now.weekday() - 2) % 7 or 7  # 2 = quarta-feira em weekday() (0=segunda)
    return now - timedelta(days=days_back)


async def test_patient_without_future_appointment_is_a_recall_candidate(client, auth_headers_a, admin_engine, tenant_a):
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Sem Retorno"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    last_visit = datetime.now(timezone.utc) - timedelta(days=20)
    await _create_appointment(admin_engine, tenant_a, patient_id, last_visit)

    weekday = last_visit.isoweekday() % 7  # converte pra convenção 0=domingo..6=sábado (EXTRACT DOW)
    response = await client.get(f"/api/v1/analytics/recall-candidates?weekday={weekday}", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 1
    assert body["items"][0]["full_name"] == "Paciente Sem Retorno"
    assert body["weekday"] == weekday


async def test_patient_with_future_scheduled_appointment_is_excluded(client, auth_headers_a, admin_engine, tenant_a):
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Já Remarcado"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    last_visit = datetime.now(timezone.utc) - timedelta(days=20)
    await _create_appointment(admin_engine, tenant_a, patient_id, last_visit)
    await _create_appointment(
        admin_engine, tenant_a, patient_id, datetime.now(timezone.utc) + timedelta(days=5), status="scheduled"
    )

    weekday = last_visit.isoweekday() % 7
    response = await client.get(f"/api/v1/analytics/recall-candidates?weekday={weekday}", headers=auth_headers_a)
    assert response.status_code == 200
    assert response.json()["total_count"] == 0


async def test_recall_candidates_filters_by_weekday_of_last_appointment(client, auth_headers_a, admin_engine, tenant_a):
    now = datetime.now(timezone.utc)
    wednesday = _last_wednesday_before(now)
    other_day = wednesday - timedelta(days=1)  # terça

    wed_patient = await client.post("/api/v1/patients", json={"full_name": "Costumava Vir Quarta"}, headers=auth_headers_a)
    other_patient = await client.post("/api/v1/patients", json={"full_name": "Costumava Vir Terça"}, headers=auth_headers_a)
    await _create_appointment(admin_engine, tenant_a, wed_patient.json()["id"], wednesday)
    await _create_appointment(admin_engine, tenant_a, other_patient.json()["id"], other_day)

    response = await client.get("/api/v1/analytics/recall-candidates?weekday=3", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    names = [i["full_name"] for i in body["items"]]
    assert "Costumava Vir Quarta" in names
    assert "Costumava Vir Terça" not in names


async def test_recall_candidates_filters_by_professional_of_last_appointment(client, auth_headers_a, admin_engine, tenant_a):
    prof_resp = await client.post("/api/v1/professionals", json={"full_name": "Dra. Livre"}, headers=auth_headers_a)
    professional_id = prof_resp.json()["id"]
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Da Dra. Livre"}, headers=auth_headers_a)
    other_patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente De Outro Profissional"}, headers=auth_headers_a)
    last_visit = datetime.now(timezone.utc) - timedelta(days=15)
    await _create_appointment(admin_engine, tenant_a, patient_resp.json()["id"], last_visit, professional_id=professional_id)
    await _create_appointment(admin_engine, tenant_a, other_patient_resp.json()["id"], last_visit, professional_id=None)

    response = await client.get(f"/api/v1/analytics/recall-candidates?professional_id={professional_id}", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 1
    assert body["items"][0]["full_name"] == "Paciente Da Dra. Livre"
    assert body["items"][0]["last_professional_name"] == "Dra. Livre"
    assert body["professional_name"] == "Dra. Livre"


async def test_recall_candidates_requires_exactly_one_filter(client, auth_headers_a):
    neither = await client.get("/api/v1/analytics/recall-candidates", headers=auth_headers_a)
    assert neither.status_code == 400

    both = await client.get(
        "/api/v1/analytics/recall-candidates?weekday=3&professional_id=11111111-1111-1111-1111-111111111111",
        headers=auth_headers_a,
    )
    assert both.status_code == 400


async def test_recall_candidates_isolates_between_tenants(client, auth_headers_a, auth_headers_b, admin_engine, tenant_a):
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Isolamento"}, headers=auth_headers_a)
    last_visit = datetime.now(timezone.utc) - timedelta(days=20)
    await _create_appointment(admin_engine, tenant_a, patient_resp.json()["id"], last_visit)

    weekday = last_visit.isoweekday() % 7
    response_b = await client.get(f"/api/v1/analytics/recall-candidates?weekday={weekday}", headers=auth_headers_b)
    assert response_b.status_code == 200
    assert response_b.json()["total_count"] == 0
