"""
tests/integration/test_crm_summary.py

Aba CRM (Roadmap "Rumo à Nota 9", Fase 5) — resposta direta ao que o
usuário apontou faltar: "ninguém sabe a média de idade dos pacientes,
ninguém sabe quanto tempo os pacientes estão sem ir à unidade". Prova
ponta a ponta os 3 números e a degradação graciosa (None, nunca 0/NaN)
quando não há amostra.
"""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text


async def test_crm_summary_is_all_none_with_no_data(client, auth_headers_a):
    response = await client.get("/api/v1/analytics/crm-summary", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "avg_patient_age_years": None,
        "avg_days_since_last_visit": None,
        "return_rate": None,
        "return_rate_sample_size": 0,
    }


async def test_crm_summary_computes_average_patient_age(client, auth_headers_a):
    today = date.today()
    await client.post(
        "/api/v1/patients", json={"full_name": "Paciente 20 anos", "birth_date": (today.replace(year=today.year - 20)).isoformat()}, headers=auth_headers_a
    )
    await client.post(
        "/api/v1/patients", json={"full_name": "Paciente 40 anos", "birth_date": (today.replace(year=today.year - 40)).isoformat()}, headers=auth_headers_a
    )
    # Sem data de nascimento — não deveria entrar na média.
    await client.post("/api/v1/patients", json={"full_name": "Paciente sem data"}, headers=auth_headers_a)

    response = await client.get("/api/v1/analytics/crm-summary", headers=auth_headers_a)
    assert response.status_code == 200
    assert response.json()["avg_patient_age_years"] == 30.0


async def test_crm_summary_computes_average_days_since_last_visit(client, auth_headers_a, admin_engine, tenant_a):
    patient = (await client.post("/api/v1/patients", json={"full_name": "Paciente A"}, headers=auth_headers_a)).json()
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.appointments (tenant_id, patient_id, scheduled_at, status) VALUES (:t, :p, :dt, 'completed')"),
            {"t": tenant_a, "p": patient["id"], "dt": datetime.now(timezone.utc) - timedelta(days=100)},
        )

    response = await client.get("/api/v1/analytics/crm-summary", headers=auth_headers_a)
    avg_days = response.json()["avg_days_since_last_visit"]
    assert avg_days is not None
    assert 99.0 <= avg_days <= 101.0


async def test_crm_summary_computes_return_rate_only_over_classified_appointments(client, auth_headers_a, admin_engine, tenant_a):
    patient = (await client.post("/api/v1/patients", json={"full_name": "Paciente B"}, headers=auth_headers_a)).json()
    async with admin_engine.begin() as conn:
        for visit_type in ("retorno", "retorno", "primeira_consulta", None):
            await conn.execute(
                text(
                    "INSERT INTO core.appointments (tenant_id, patient_id, scheduled_at, status, visit_type) "
                    "VALUES (:t, :p, :dt, 'completed', :vt)"
                ),
                {"t": tenant_a, "p": patient["id"], "dt": datetime.now(timezone.utc), "vt": visit_type},
            )

    response = await client.get("/api/v1/analytics/crm-summary", headers=auth_headers_a)
    body = response.json()
    # 2 retornos em 3 atendimentos CLASSIFICADOS (o NULL não conta nem no
    # numerador nem no denominador).
    assert body["return_rate_sample_size"] == 3
    assert body["return_rate"] == 2 / 3


async def test_crm_summary_isolates_between_tenants(client, auth_headers_a, auth_headers_b, admin_engine, tenant_a):
    today = date.today()
    await client.post(
        "/api/v1/patients", json={"full_name": "Paciente Tenant A", "birth_date": (today.replace(year=today.year - 50)).isoformat()}, headers=auth_headers_a
    )

    response_b = await client.get("/api/v1/analytics/crm-summary", headers=auth_headers_b)
    assert response_b.json()["avg_patient_age_years"] is None
