"""
tests/integration/test_product_roi.py

GET /analytics/product-roi — Épico F4.4 do Plano Diretor ("Prova de ROI
do próprio produto"): soma três componentes independentes e
CUMULATIVOS (nunca uma janela de período) — valor protegido pelo motor
anti-glosa, valor recuperado em recursos de glosa ganhos, e ganho real
medido em insights que um gestor fechou o ciclo (F1.2).
"""
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Unimed Nacional", normalized_key="unimed_nacional") -> str:
    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :name, :key)"),
            {"id": plan_id, "t": tenant_id, "name": display_name, "key": normalized_key},
        )
    return plan_id


async def _create_contract(admin_engine, tenant_id, plan_id, procedure_code, agreed_value):
    contract_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.contracts (id, tenant_id, insurance_plan_id, valid_from, status) "
                "VALUES (:id, :t, :plan, '2026-01-01', 'homologado')"
            ),
            {"id": contract_id, "t": tenant_id, "plan": plan_id},
        )
        await conn.execute(
            text(
                "INSERT INTO core.contract_items (tenant_id, contract_id, tuss_code, agreed_price) "
                "VALUES (:t, :contract, :code, :value)"
            ),
            {"t": tenant_id, "contract": contract_id, "code": procedure_code, "value": agreed_value},
        )
    return contract_id


async def test_product_roi_is_all_zero_for_a_fresh_tenant(client, auth_headers_a):
    response = await client.get("/api/v1/analytics/product-roi", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["protected_from_denial_value"] == 0.0
    assert body["recovered_appeals_value"] == 0.0
    assert body["recovered_appeals_count"] == 0
    assert body["realized_insight_outcomes_value"] == 0.0
    assert body["realized_insight_outcomes_count"] == 0
    assert body["total_roi_value"] == 0.0
    assert body["tracking_since"] is None


async def test_product_roi_includes_value_protected_from_overcharge(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_contract(admin_engine, tenant_a, plan_id, procedure_code="20202020", agreed_value=150.0)

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente ROI"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    appt_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "20202020",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )
    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appt_resp.json()["id"], "insurance_plan_id": plan_id, "charged_value": 180.0},
        headers=auth_headers_a,
    )
    assert billing_resp.json()["value_saved_by_correction"] == 30.0

    response = await client.get("/api/v1/analytics/product-roi", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["protected_from_denial_value"] == 30.0
    assert body["total_roi_value"] == 30.0
    assert body["tracking_since"] == date.today().isoformat()


async def _create_billing(client, auth_headers, plan_id: str, *, charged_value: float = 150.0) -> str:
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente ROI Recurso"}, headers=auth_headers)
    patient_id = patient_resp.json()["id"]
    appt_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
            "cid_code": "J06",
        },
        headers=auth_headers,
    )
    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appt_resp.json()["id"], "insurance_plan_id": plan_id, "charged_value": charged_value},
        headers=auth_headers,
    )
    assert billing_resp.status_code == 201, billing_resp.text
    return billing_resp.json()["id"]


async def test_product_roi_includes_recovered_appeals_value(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, auth_headers_a, plan_id, charged_value=400.0)

    create_resp = await client.post(
        "/api/v1/denial-appeals",
        json={"billing_id": billing_id, "appeal_type": "administrativa", "denied_at": date.today().isoformat()},
        headers=auth_headers_a,
    )
    appeal_id = create_resp.json()["id"]
    await client.post(f"/api/v1/denial-appeals/{appeal_id}/file", json={}, headers=auth_headers_a)
    resolve_resp = await client.post(
        f"/api/v1/denial-appeals/{appeal_id}/resolve", json={"status": "deferido"}, headers=auth_headers_a
    )
    assert resolve_resp.status_code == 200

    response = await client.get("/api/v1/analytics/product-roi", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["recovered_appeals_value"] == 400.0
    assert body["recovered_appeals_count"] == 1
    assert body["total_roi_value"] == 400.0


async def test_product_roi_excludes_denied_or_open_appeals(client, auth_headers_a, admin_engine, tenant_a):
    """Só recurso GANHO (deferido) conta como recuperado — indeferido ou
    ainda aberto não é dinheiro recuperado de verdade."""
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, auth_headers_a, plan_id, charged_value=400.0)

    create_resp = await client.post(
        "/api/v1/denial-appeals",
        json={"billing_id": billing_id, "appeal_type": "administrativa", "denied_at": date.today().isoformat()},
        headers=auth_headers_a,
    )
    appeal_id = create_resp.json()["id"]
    await client.post(f"/api/v1/denial-appeals/{appeal_id}/file", json={}, headers=auth_headers_a)
    await client.post(f"/api/v1/denial-appeals/{appeal_id}/resolve", json={"status": "indeferido"}, headers=auth_headers_a)

    response = await client.get("/api/v1/analytics/product-roi", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["recovered_appeals_value"] == 0.0
    assert body["recovered_appeals_count"] == 0


async def _get_owner_user_id(admin_engine, tenant_id: str) -> str:
    async with admin_engine.begin() as conn:
        row = (await conn.execute(text("SELECT id FROM core.users WHERE tenant_id = :t LIMIT 1"), {"t": tenant_id})).mappings().first()
    return str(row["id"])


async def _seed_resolved_reevaluated_outcome(
    admin_engine, tenant_id: str, user_id: str, *, financial_impact: float, resolved_metric_value: float
) -> None:
    outcome_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO core.insight_outcomes
                    (id, tenant_id, insight_key, source, category, severity, title, message,
                     financial_impact_snapshot, status, resolution_note, resolved_at,
                     resolved_metric_value, reevaluated_at, created_by)
                VALUES
                    (:id, :t, 'oportunidade-renegociacao-x', 'insight', 'estrategia', 'warning',
                     'Renegocie o contrato com o convênio X', 'mensagem',
                     :impact, 'resolvido', 'renegociado', :resolved_at,
                     :resolved_metric, :reevaluated_at, :user_id)
                """
            ),
            {
                "id": outcome_id,
                "t": tenant_id,
                "impact": financial_impact,
                "resolved_at": now - timedelta(days=100),
                "resolved_metric": resolved_metric_value,
                "reevaluated_at": now,
                "user_id": user_id,
            },
        )


async def test_product_roi_includes_realized_insight_outcome_gain(client, auth_headers_a, admin_engine, tenant_a):
    user_id = await _get_owner_user_id(admin_engine, tenant_a)
    # Insight tinha R$1000 de impacto projetado; depois de resolvido e
    # reavaliado, o indicador caiu pra R$300 -> ganho real de R$700.
    await _seed_resolved_reevaluated_outcome(admin_engine, tenant_a, user_id, financial_impact=1000.0, resolved_metric_value=300.0)

    response = await client.get("/api/v1/analytics/product-roi", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["realized_insight_outcomes_value"] == 700.0
    assert body["realized_insight_outcomes_count"] == 1
    assert body["total_roi_value"] == 700.0


async def test_product_roi_excludes_outcomes_not_yet_reevaluated(client, auth_headers_a, admin_engine, tenant_a):
    """Nunca soma uma promessa ainda não conferida — só outcomes JÁ
    reavaliados entram no total."""
    user_id = await _get_owner_user_id(admin_engine, tenant_a)
    outcome_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO core.insight_outcomes
                    (id, tenant_id, insight_key, source, category, severity, title, message,
                     financial_impact_snapshot, status, created_by)
                VALUES
                    (:id, :t, 'oportunidade-renegociacao-y', 'insight', 'estrategia', 'warning',
                     'Renegocie', 'mensagem', 1000.0, 'resolvido', :user_id)
                """
            ),
            {"id": outcome_id, "t": tenant_a, "user_id": user_id},
        )

    response = await client.get("/api/v1/analytics/product-roi", headers=auth_headers_a)
    assert response.status_code == 200
    assert response.json()["realized_insight_outcomes_value"] == 0.0


async def test_product_roi_sums_all_three_components_together(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_contract(admin_engine, tenant_a, plan_id, procedure_code="20202020", agreed_value=150.0)
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente ROI Total"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    appt_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "20202020",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )
    await client.post(
        "/api/v1/billing",
        json={"appointment_id": appt_resp.json()["id"], "insurance_plan_id": plan_id, "charged_value": 180.0},
        headers=auth_headers_a,
    )  # +30.0 protegido

    billing_id = await _create_billing(client, auth_headers_a, plan_id, charged_value=400.0)
    create_resp = await client.post(
        "/api/v1/denial-appeals",
        json={"billing_id": billing_id, "appeal_type": "administrativa", "denied_at": date.today().isoformat()},
        headers=auth_headers_a,
    )
    appeal_id = create_resp.json()["id"]
    await client.post(f"/api/v1/denial-appeals/{appeal_id}/file", json={}, headers=auth_headers_a)
    await client.post(f"/api/v1/denial-appeals/{appeal_id}/resolve", json={"status": "deferido"}, headers=auth_headers_a)  # +400.0

    user_id = await _get_owner_user_id(admin_engine, tenant_a)
    await _seed_resolved_reevaluated_outcome(admin_engine, tenant_a, user_id, financial_impact=1000.0, resolved_metric_value=300.0)  # +700.0

    response = await client.get("/api/v1/analytics/product-roi", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["total_roi_value"] == 30.0 + 400.0 + 700.0


async def test_atendimento_cannot_access_product_roi(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao@product-roi.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.get("/api/v1/analytics/product-roi", headers=headers)
    assert response.status_code == 403


async def test_product_roi_isolates_between_tenants(client, auth_headers_a, auth_headers_b, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_contract(admin_engine, tenant_a, plan_id, procedure_code="20202020", agreed_value=150.0)
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Isolamento ROI"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    appt_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "20202020",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )
    await client.post(
        "/api/v1/billing",
        json={"appointment_id": appt_resp.json()["id"], "insurance_plan_id": plan_id, "charged_value": 180.0},
        headers=auth_headers_a,
    )

    response_b = await client.get("/api/v1/analytics/product-roi", headers=auth_headers_b)
    assert response_b.status_code == 200
    assert response_b.json()["protected_from_denial_value"] == 0.0
