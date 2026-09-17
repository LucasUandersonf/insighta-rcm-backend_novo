"""
tests/integration/test_opme_documentation_confirmation.py

POST /billing/{id}/confirm-clinical-documentation — Épico F2.3 do Plano
Diretor ("Auditoria documental leve: prontuário × conta"). Versão
RESTRITA (sem NLP semântico): confirma se existe registro de
prescrição/evolução no prontuário sustentando um item OPME, antes da
guia ir pro convênio.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Unimed OPME", normalized_key="unimed_opme") -> str:
    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :name, :key)"),
            {"id": plan_id, "t": tenant_id, "name": display_name, "key": normalized_key},
        )
    return plan_id


async def _create_billing(client, auth_headers, plan_id: str, *, item_type: str | None = "material_opme", charged_value: float = 800.0) -> str:
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente OPME"}, headers=auth_headers)
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
        json={
            "appointment_id": appt_resp.json()["id"],
            "insurance_plan_id": plan_id,
            "charged_value": charged_value,
            "item_type": item_type,
        },
        headers=auth_headers,
    )
    assert billing_resp.status_code == 201, billing_resp.text
    return billing_resp.json()["id"]


async def test_new_opme_billing_has_no_documentation_confirmation_by_default(client, auth_headers_a, admin_engine, tenant_a):
    """Estado inicial é NULL, nunca False — ver DECISÃO em
    044_opme_documentation_confirmation.sql."""
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, auth_headers_a, plan_id)

    async with admin_engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT clinical_documentation_confirmed FROM core.billing WHERE id = :id"), {"id": billing_id}
            )
        ).mappings().first()
    assert row["clinical_documentation_confirmed"] is None


async def test_confirm_clinical_documentation_found(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, auth_headers_a, plan_id)

    resp = await client.post(
        f"/api/v1/billing/{billing_id}/confirm-clinical-documentation", json={"found": True}, headers=auth_headers_a
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["clinical_documentation_confirmed"] is True
    assert body["clinical_documentation_confirmed_at"] is not None


async def test_confirm_clinical_documentation_not_found_is_a_valid_state(client, auth_headers_a, admin_engine, tenant_a):
    """FALSE é um estado válido e distinto de NULL — risco de glosa
    documental PROVADO, não só "ainda não conferido"."""
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, auth_headers_a, plan_id)

    resp = await client.post(
        f"/api/v1/billing/{billing_id}/confirm-clinical-documentation", json={"found": False}, headers=auth_headers_a
    )
    assert resp.status_code == 200
    assert resp.json()["clinical_documentation_confirmed"] is False


async def test_cannot_confirm_clinical_documentation_on_a_non_opme_billing(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, auth_headers_a, plan_id, item_type="procedimento")

    resp = await client.post(
        f"/api/v1/billing/{billing_id}/confirm-clinical-documentation", json={"found": True}, headers=auth_headers_a
    )
    assert resp.status_code == 422


async def test_confirm_clinical_documentation_404_for_unknown_billing(client, auth_headers_a):
    resp = await client.post(
        "/api/v1/billing/00000000-0000-0000-0000-000000000000/confirm-clinical-documentation",
        json={"found": True},
        headers=auth_headers_a,
    )
    assert resp.status_code == 404


async def test_atendimento_cannot_confirm_clinical_documentation(client, admin_engine, tenant_a, auth_headers_a):
    """DIFERENTE do RBAC de confirm-coparticipation — esta é uma tarefa
    de auditoria de faturamento (financeiro/admin/owner), não da recepção."""
    from tests.conftest import _insert_user, _login

    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, auth_headers_a, plan_id)

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao@opme-doc.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(f"/api/v1/billing/{billing_id}/confirm-clinical-documentation", json={"found": True}, headers=headers)
    assert resp.status_code == 403


async def test_confirmation_is_audited(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, auth_headers_a, plan_id)

    await client.post(f"/api/v1/billing/{billing_id}/confirm-clinical-documentation", json={"found": True}, headers=auth_headers_a)

    audit_resp = await client.get(
        "/api/v1/audit-log?entity_type=billing&action=clinical_documentation_confirmed", headers=auth_headers_a
    )
    assert audit_resp.status_code == 200
    entries = audit_resp.json()["items"]
    assert any(e["entity_id"] == billing_id for e in entries)


# ---------------------------------------------------------------------
# GET /analytics/smart-insights — insight de documentação OPME não conferida
# ---------------------------------------------------------------------


def _window() -> tuple[str, str]:
    today = date.today()
    return (today - timedelta(days=1)).isoformat(), (today + timedelta(days=1)).isoformat()


async def test_unconfirmed_opme_documentation_insight_fires_with_enough_sample(
    client, auth_headers_a, admin_engine, tenant_a
):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    # 2 itens OPME, nenhum conferido (amostra mínima é 2, ver
    # _MIN_OPME_DOCUMENTATION_SAMPLE).
    for _ in range(2):
        await _create_billing(client, auth_headers_a, plan_id, charged_value=800.0)

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    insights = response.json()["insights"]
    unconfirmed = next((i for i in insights if "sem conferência documental" in i["title"].lower()), None)
    assert unconfirmed is not None
    assert unconfirmed["financial_impact"] == 1600.0  # 2 * 800.0


async def test_unconfirmed_opme_documentation_insight_excludes_confirmed_billings(
    client, auth_headers_a, admin_engine, tenant_a
):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_ids = [await _create_billing(client, auth_headers_a, plan_id) for _ in range(2)]
    for billing_id in billing_ids:
        await client.post(
            f"/api/v1/billing/{billing_id}/confirm-clinical-documentation", json={"found": True}, headers=auth_headers_a
        )

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    insights = response.json()["insights"]
    assert not any("sem conferência documental" in i["title"].lower() for i in insights)


async def test_unconfirmed_opme_documentation_insight_includes_confirmed_not_found(
    client, auth_headers_a, admin_engine, tenant_a
):
    """FALSE (registro comprovadamente ausente) também conta como 'não
    conferido' — o insight cobre os dois estados de risco."""
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_ids = [await _create_billing(client, auth_headers_a, plan_id) for _ in range(2)]
    for billing_id in billing_ids:
        await client.post(
            f"/api/v1/billing/{billing_id}/confirm-clinical-documentation", json={"found": False}, headers=auth_headers_a
        )

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    insights = response.json()["insights"]
    unconfirmed = next((i for i in insights if "sem conferência documental" in i["title"].lower()), None)
    assert unconfirmed is not None
    assert unconfirmed["financial_impact"] == 1600.0


async def test_unconfirmed_opme_documentation_insight_absent_below_min_sample(
    client, auth_headers_a, admin_engine, tenant_a
):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    # Só 1 item OPME sem conferência — abaixo da amostra mínima (2).
    await _create_billing(client, auth_headers_a, plan_id)

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    insights = response.json()["insights"]
    assert not any("sem conferência documental" in i["title"].lower() for i in insights)


async def test_unconfirmed_opme_documentation_insight_ignores_non_opme_items(
    client, auth_headers_a, admin_engine, tenant_a
):
    """Um procedimento comum (não-OPME) nunca entra nessa soma, mesmo
    sem `clinical_documentation_confirmed` preenchido — a coluna só tem
    sentido de negócio pra item_type='material_opme'."""
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    for _ in range(5):
        await _create_billing(client, auth_headers_a, plan_id, item_type="procedimento", charged_value=300.0)

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    insights = response.json()["insights"]
    assert not any("sem conferência documental" in i["title"].lower() for i in insights)
