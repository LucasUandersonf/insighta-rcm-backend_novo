"""
tests/integration/test_coparticipation_confirmation.py

POST /billing/{id}/confirm-coparticipation — Épico F4.2 do Plano Diretor
("Fechar lacunas operacionais"): fecha a lacuna que os insights de
coparticipação já existentes deixavam em aberto — o sistema sabia
QUANTO foi cobrado de coparticipação, nunca se foi de fato RECEBIDO.
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


async def _create_billing(client, auth_headers, plan_id: str, *, coparticipation_value: float | None = 25.0) -> str:
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Coparticipação"}, headers=auth_headers)
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
            "charged_value": 150.0,
            "coparticipation_value": coparticipation_value,
        },
        headers=auth_headers,
    )
    assert billing_resp.status_code == 201, billing_resp.text
    return billing_resp.json()["id"]


async def test_new_billing_has_no_coparticipation_confirmation_by_default(client, auth_headers_a, admin_engine, tenant_a):
    """Estado inicial é NULL, nunca False — ver DECISÃO em
    043_coparticipation_confirmation.sql."""
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, auth_headers_a, plan_id)

    resp = await client.get(f"/api/v1/billing/search?q=Paciente", headers=auth_headers_a)
    assert resp.status_code == 200

    async with admin_engine.begin() as conn:
        row = (await conn.execute(text("SELECT coparticipation_received FROM core.billing WHERE id = :id"), {"id": billing_id})).mappings().first()
    assert row["coparticipation_received"] is None


async def test_confirm_coparticipation_received(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, auth_headers_a, plan_id)

    resp = await client.post(
        f"/api/v1/billing/{billing_id}/confirm-coparticipation", json={"received": True}, headers=auth_headers_a
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["coparticipation_received"] is True
    assert body["coparticipation_confirmed_at"] is not None


async def test_confirm_coparticipation_not_received_is_a_valid_state(client, auth_headers_a, admin_engine, tenant_a):
    """FALSE é um estado válido e distinto de NULL — vazamento de
    receita PROVADO, não só "ainda não confirmado"."""
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, auth_headers_a, plan_id)

    resp = await client.post(
        f"/api/v1/billing/{billing_id}/confirm-coparticipation", json={"received": False}, headers=auth_headers_a
    )
    assert resp.status_code == 200
    assert resp.json()["coparticipation_received"] is False


async def test_cannot_confirm_coparticipation_without_a_charged_value(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, auth_headers_a, plan_id, coparticipation_value=None)

    resp = await client.post(
        f"/api/v1/billing/{billing_id}/confirm-coparticipation", json={"received": True}, headers=auth_headers_a
    )
    assert resp.status_code == 422


async def test_confirm_coparticipation_404_for_unknown_billing(client, auth_headers_a):
    resp = await client.post(
        "/api/v1/billing/00000000-0000-0000-0000-000000000000/confirm-coparticipation",
        json={"received": True},
        headers=auth_headers_a,
    )
    assert resp.status_code == 404


async def test_atendimento_can_confirm_coparticipation(client, admin_engine, tenant_a, auth_headers_a):
    """DIFERENTE do RBAC do resto de billing.py — esta confirmação
    acontece na recepção, no momento do atendimento (Épico F4.2)."""
    from tests.conftest import _insert_user, _login

    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, auth_headers_a, plan_id)

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao@coparticipacao.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(f"/api/v1/billing/{billing_id}/confirm-coparticipation", json={"received": True}, headers=headers)
    assert resp.status_code == 200


# ---------------------------------------------------------------------
# "Mapa de Dados Insighta" — Domínio Financeiro particular (Onda 1):
# confirmar coparticipação recebida é o checkout real do particular —
# ponto de captura natural pra COMO o paciente pagou.
# ---------------------------------------------------------------------


async def test_confirm_coparticipation_received_captures_payment_method(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, auth_headers_a, plan_id)

    resp = await client.post(
        f"/api/v1/billing/{billing_id}/confirm-coparticipation",
        json={"received": True, "payment_method": "cartao_credito", "installments": 2},
        headers=auth_headers_a,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["payment_method"] == "cartao_credito"
    assert body["installments"] == 2


async def test_confirm_coparticipation_not_received_ignores_payment_method(client, auth_headers_a, admin_engine, tenant_a):
    """Nada foi pago -> não há como registrar "como foi pago", mesmo que
    o campo venha preenchido no corpo da requisição por engano."""
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, auth_headers_a, plan_id)

    resp = await client.post(
        f"/api/v1/billing/{billing_id}/confirm-coparticipation",
        json={"received": False, "payment_method": "pix"},
        headers=auth_headers_a,
    )
    assert resp.status_code == 200
    assert resp.json()["payment_method"] is None


async def test_confirm_coparticipation_rejects_unknown_payment_method(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, auth_headers_a, plan_id)

    resp = await client.post(
        f"/api/v1/billing/{billing_id}/confirm-coparticipation",
        json={"received": True, "payment_method": "criptomoeda"},
        headers=auth_headers_a,
    )
    assert resp.status_code == 422


async def test_confirmation_is_audited(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, auth_headers_a, plan_id)

    await client.post(f"/api/v1/billing/{billing_id}/confirm-coparticipation", json={"received": True}, headers=auth_headers_a)

    audit_resp = await client.get(
        "/api/v1/audit-log?entity_type=billing&action=coparticipation_confirmed", headers=auth_headers_a
    )
    assert audit_resp.status_code == 200
    entries = audit_resp.json()["items"]
    assert any(e["entity_id"] == billing_id for e in entries)


# ---------------------------------------------------------------------
# GET /analytics/smart-insights — insight de coparticipação não confirmada
# ---------------------------------------------------------------------


def _window() -> tuple[str, str]:
    today = date.today()
    return (today - timedelta(days=1)).isoformat(), (today + timedelta(days=1)).isoformat()


async def test_unconfirmed_coparticipation_insight_fires_with_enough_sample(
    client, auth_headers_a, admin_engine, tenant_a
):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    # 5 billings com coparticipação cobrada, nenhuma confirmada.
    for _ in range(5):
        await _create_billing(client, auth_headers_a, plan_id)

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    insights = response.json()["insights"]
    unconfirmed = next((i for i in insights if "ainda não foi confirmada" in i["title"].lower()), None)
    assert unconfirmed is not None
    assert unconfirmed["financial_impact"] == 125.0  # 5 * 25.0


async def test_unconfirmed_coparticipation_insight_excludes_confirmed_billings(
    client, auth_headers_a, admin_engine, tenant_a
):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_ids = [await _create_billing(client, auth_headers_a, plan_id) for _ in range(5)]
    # Confirma TODAS como recebidas -> nada deveria ficar pendente.
    for billing_id in billing_ids:
        await client.post(f"/api/v1/billing/{billing_id}/confirm-coparticipation", json={"received": True}, headers=auth_headers_a)

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    insights = response.json()["insights"]
    assert not any("ainda não foi confirmada" in i["title"].lower() for i in insights)


async def test_unconfirmed_coparticipation_insight_includes_confirmed_not_received(
    client, auth_headers_a, admin_engine, tenant_a
):
    """FALSE (vazamento provado) também conta como 'não confirmado como
    recebido' — o insight cobre os dois estados de risco."""
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_ids = [await _create_billing(client, auth_headers_a, plan_id) for _ in range(5)]
    for billing_id in billing_ids:
        await client.post(f"/api/v1/billing/{billing_id}/confirm-coparticipation", json={"received": False}, headers=auth_headers_a)

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    insights = response.json()["insights"]
    unconfirmed = next((i for i in insights if "ainda não foi confirmada" in i["title"].lower()), None)
    assert unconfirmed is not None
    assert unconfirmed["financial_impact"] == 125.0


async def test_unconfirmed_coparticipation_insight_absent_below_min_sample(
    client, auth_headers_a, admin_engine, tenant_a
):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    # Só 2 billings sem confirmação — abaixo da amostra mínima (5, mesmo
    # critério de _MIN_COPARTICIPATION_SAMPLE).
    for _ in range(2):
        await _create_billing(client, auth_headers_a, plan_id)

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    insights = response.json()["insights"]
    assert not any("ainda não foi confirmada" in i["title"].lower() for i in insights)
