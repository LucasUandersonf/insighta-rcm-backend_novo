"""
tests/integration/test_health_score.py

Ponta a ponta via HTTP para a Nota de Saúde Financeira — prova que
AnalyticsService.get_health_score de fato agrega dado real do banco
(componente de amostra insuficiente excluído, RLS isola entre tenants).
O motor puro (regras de score) já é coberto sem banco em
tests/test_health_score_engine.py.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Unimed Nacional", normalized_key="unimed_nacional") -> str:
    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :name, :key)"),
            {"id": plan_id, "t": tenant_id, "name": display_name, "key": normalized_key},
        )
    return plan_id


async def test_health_score_is_none_for_tenant_without_any_data(client, auth_headers_a):
    response = await client.get("/api/v1/analytics/health-score", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["score"] is None
    assert body["components"] == []
    assert body["window_days"] == 90


async def test_health_score_reflects_high_risk_billing(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Nota de Saúde"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
            # cid_code omitido -> billing nasce com denial_risk_level "high"
        },
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]
    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 150.0},
        headers=auth_headers_a,
    )
    assert billing_resp.json()["denial_risk_level"] == "high"

    response = await client.get("/api/v1/analytics/health-score", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["score"] is not None
    denial_component = next(c for c in body["components"] if c["key"] == "denial")
    assert denial_component["rate"] == 1.0  # 100% do faturamento no período está em risco alto
    assert denial_component["sub_score"] == 0.0  # acima do teto (25%) -> pior nota possível
    # Sem agendamento resolvido nem recurso decidido no período -> só o componente de glosa entra.
    assert {c["key"] for c in body["components"]} == {"denial"}


async def test_health_score_no_show_component_uses_resolved_appointments_only(client, auth_headers_a, admin_engine, tenant_a):
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Falta"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    now = datetime.now(timezone.utc) - timedelta(days=1)

    async with admin_engine.begin() as conn:
        for status in ("no_show", "completed", "completed", "completed"):
            await conn.execute(
                text("INSERT INTO core.appointments (tenant_id, patient_id, scheduled_at, status) VALUES (:t, :p, :dt, :s)"),
                {"t": tenant_a, "p": patient_id, "dt": now, "s": status},
            )
        # 'scheduled' não deveria contar (sem desfecho ainda).
        await conn.execute(
            text("INSERT INTO core.appointments (tenant_id, patient_id, scheduled_at, status) VALUES (:t, :p, :dt, 'scheduled')"),
            {"t": tenant_a, "p": patient_id, "dt": now},
        )

    response = await client.get("/api/v1/analytics/health-score", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    no_show_component = next(c for c in body["components"] if c["key"] == "no_show")
    assert round(no_show_component["rate"], 4) == round(1 / 4, 4)  # 1 falta em 4 resolvidos


async def test_health_score_appeal_component_needs_minimum_sample(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Recurso"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]
    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 100.0},
        headers=auth_headers_a,
    )
    billing_id = billing_resp.json()["id"]

    # Só 2 recursos decididos (abaixo do mínimo de 3, ver _MIN_APPEAL_SAMPLE)
    # -> componente "appeal" não deveria aparecer.
    now = datetime.now(timezone.utc)
    async with admin_engine.begin() as conn:
        for status in ("deferido", "indeferido"):
            await conn.execute(
                text(
                    "INSERT INTO core.denial_appeals "
                    "(tenant_id, billing_id, appeal_type, denied_at, deadline_at, status, resolved_at) "
                    "VALUES (:t, :b, 'administrativa', :d, :dl, :s, :r)"
                ),
                {"t": tenant_a, "b": billing_id, "d": now.date(), "dl": (now + timedelta(days=30)).date(), "s": status, "r": now},
            )

    response = await client.get("/api/v1/analytics/health-score", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert "appeal" not in {c["key"] for c in body["components"]}


async def _insert_health_score_snapshot(admin_engine, tenant_id: str, *, snapshot_month, score) -> None:
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.health_score_snapshots (tenant_id, snapshot_month, score) "
                "VALUES (:t, :m, :s)"
            ),
            {"t": tenant_id, "m": snapshot_month, "s": score},
        )


async def test_health_score_has_no_trend_without_any_reference_snapshot(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Sem Histórico"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]
    await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 100.0},
        headers=auth_headers_a,
    )

    response = await client.get("/api/v1/analytics/health-score", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["score"] is not None
    assert body["trend"] is None  # base nova, sem 3 meses de histórico ainda


async def test_health_score_trend_compares_against_reference_snapshot(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Tendência"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]
    await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 100.0},
        headers=auth_headers_a,
    )

    reference_month = (date.today() - timedelta(days=95)).replace(day=1)
    await _insert_health_score_snapshot(admin_engine, tenant_a, snapshot_month=reference_month, score=40.0)

    response = await client.get("/api/v1/analytics/health-score", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["score"] is not None
    assert body["trend"] is not None
    assert body["trend"]["reference_score"] == 40.0
    assert body["trend"]["reference_month"] == reference_month.isoformat()
    assert body["trend"]["delta"] == pytest.approx(body["score"] - 40.0)


async def test_health_score_trend_picks_the_most_recent_snapshot_at_or_before_the_reference_mark(
    client, auth_headers_a, admin_engine, tenant_a
):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Vários Snapshots"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]
    await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 100.0},
        headers=auth_headers_a,
    )

    # Um snapshot antigo o suficiente (referência correta) e um mais
    # recente que NÃO deveria ser escolhido (mais próximo de hoje que a
    # marca de ~90 dias, ainda "no futuro" em relação à referência).
    old_month = (date.today() - timedelta(days=200)).replace(day=1)
    recent_month = (date.today() - timedelta(days=30)).replace(day=1)
    await _insert_health_score_snapshot(admin_engine, tenant_a, snapshot_month=old_month, score=10.0)
    await _insert_health_score_snapshot(admin_engine, tenant_a, snapshot_month=recent_month, score=90.0)

    response = await client.get("/api/v1/analytics/health-score", headers=auth_headers_a)
    assert response.status_code == 200
    trend = response.json()["trend"]
    assert trend["reference_score"] == 10.0


async def test_health_score_trend_ignores_reference_snapshot_with_null_score(
    client, auth_headers_a, admin_engine, tenant_a
):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Snapshot Nulo"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]
    await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 100.0},
        headers=auth_headers_a,
    )

    reference_month = (date.today() - timedelta(days=95)).replace(day=1)
    # Amostra insuficiente naquele mês -> score gravado como NULL. Nunca
    # deveria virar uma tendência "contra zero".
    await _insert_health_score_snapshot(admin_engine, tenant_a, snapshot_month=reference_month, score=None)

    response = await client.get("/api/v1/analytics/health-score", headers=auth_headers_a)
    assert response.status_code == 200
    assert response.json()["trend"] is None


async def test_health_score_isolates_between_tenants(client, auth_headers_a, auth_headers_b, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Isolamento"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
        },
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]
    await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 150.0},
        headers=auth_headers_a,
    )

    response_b = await client.get("/api/v1/analytics/health-score", headers=auth_headers_b)
    assert response_b.status_code == 200
    # Tenant B não tem nenhum dado próprio -> nunca deveria enxergar o
    # billing de risco alto do tenant A (RLS).
    assert response_b.json()["score"] is None
