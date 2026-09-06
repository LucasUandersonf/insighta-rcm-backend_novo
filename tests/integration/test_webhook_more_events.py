"""
tests/integration/test_webhook_more_events.py — segundo e terceiro
eventos ligados ao motor de webhooks (ver DECISÃO em
app/services/webhook_dispatch_service.py): "denial_appeal.resolved" e
"no_show_risk.high". O primeiro evento ("billing.held_for_review") já é
coberto em test_webhook_subscriptions.py — este arquivo prova só o que é
NOVO: os dois gatilhos adicionais e que nenhum dos dois vaza PII.

Mesma técnica de mock de test_webhook_subscriptions.py (substitui
httpx.AsyncClient inteiro).
"""
import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text


class _FakeResponse:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code


class _FakeAsyncClient:
    calls: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, content=None, headers=None):
        self.__class__.calls.append({"url": url, "content": content, "headers": headers})
        return _FakeResponse(200)


@pytest.fixture(autouse=True)
def _fake_webhook_delivery(monkeypatch):
    from app.services import webhook_dispatch_service as dispatch_module

    _FakeAsyncClient.calls = []
    monkeypatch.setattr(dispatch_module.httpx, "AsyncClient", _FakeAsyncClient)
    yield


async def _create_webhook(client, auth_headers, event_types: list[str] | None = None) -> str:
    resp = await client.post(
        "/api/v1/integrations/webhooks",
        json={"name": "Slack de teste", "url": "https://hooks.slack.com/services/xyz", "event_types": event_types or []},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["secret"]


def _delivered_events() -> list[dict]:
    return [json.loads(c["content"]) for c in _FakeAsyncClient.calls]


# =====================================================================
# denial_appeal.resolved
# =====================================================================


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Amil One", normalized_key="amil_one") -> str:
    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :n, :k)"),
            {"id": plan_id, "t": tenant_id, "n": display_name, "k": normalized_key},
        )
    return plan_id


async def _create_billing_for_appeal(client, auth_headers, plan_id: str) -> str:
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Recurso Webhook"}, headers=auth_headers)
    patient_id = patient_resp.json()["id"]
    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
            "cid_code": "Z00.0",
        },
        headers=auth_headers,
    )
    appointment_id = appointment_resp.json()["id"]
    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 150.0},
        headers=auth_headers,
    )
    assert billing_resp.status_code == 201
    return billing_resp.json()["id"]


async def test_denial_appeal_resolved_dispatches_webhook_without_pii(client, auth_headers_a, admin_engine, tenant_a):
    await _create_webhook(client, auth_headers_a, event_types=["denial_appeal.resolved"])
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing_for_appeal(client, auth_headers_a, plan_id)

    create_resp = await client.post(
        "/api/v1/denial-appeals",
        json={"billing_id": billing_id, "appeal_type": "medica", "denied_at": date.today().isoformat()},
        headers=auth_headers_a,
    )
    appeal_id = create_resp.json()["id"]
    await client.post(f"/api/v1/denial-appeals/{appeal_id}/file", json={}, headers=auth_headers_a)

    # billing.held_for_review pode ter disparado na criação do faturamento
    # (não é o foco aqui) — zera antes do resolve para isolar o evento sob teste.
    _FakeAsyncClient.calls = []

    resolve_resp = await client.post(
        f"/api/v1/denial-appeals/{appeal_id}/resolve",
        json={"status": "deferido", "resolution_notes": "Paciente Recurso Webhook ligou confirmando o CPF 123.456.789-00."},
        headers=auth_headers_a,
    )
    assert resolve_resp.status_code == 200

    events = _delivered_events()
    assert len(events) == 1
    assert events[0]["event_type"] == "denial_appeal.resolved"
    data = events[0]["data"]
    assert data["appeal_id"] == appeal_id
    assert data["billing_id"] == billing_id
    assert data["status"] == "deferido"
    assert data["previous_status"] == "protocolado"
    # Nunca resolution_notes/operator_denial_reason no corpo — texto
    # livre digitado por humano pode conter nome/CPF de paciente.
    assert "resolution_notes" not in data
    assert "operator_denial_reason" not in data
    raw_body = _FakeAsyncClient.calls[0]["content"].decode("utf-8")
    assert "Paciente Recurso Webhook" not in raw_body
    assert "123.456.789-00" not in raw_body


async def test_denial_appeal_nip_escalation_also_dispatches(client, auth_headers_a, admin_engine, tenant_a):
    await _create_webhook(client, auth_headers_a, event_types=["denial_appeal.resolved"])
    plan_id = await _create_insurance_plan(admin_engine, tenant_a, display_name="Bradesco", normalized_key="bradesco")
    billing_id = await _create_billing_for_appeal(client, auth_headers_a, plan_id)

    create_resp = await client.post(
        "/api/v1/denial-appeals",
        json={"billing_id": billing_id, "appeal_type": "medica", "denied_at": date.today().isoformat()},
        headers=auth_headers_a,
    )
    appeal_id = create_resp.json()["id"]
    await client.post(f"/api/v1/denial-appeals/{appeal_id}/file", json={}, headers=auth_headers_a)
    await client.post(f"/api/v1/denial-appeals/{appeal_id}/resolve", json={"status": "indeferido"}, headers=auth_headers_a)
    _FakeAsyncClient.calls = []

    # Escalada indeferido -> nip_aberta NÃO é uma resolução terminal, mas
    # ainda é uma mudança de estado real — o motor dispara mesmo assim.
    escalate_resp = await client.post(
        f"/api/v1/denial-appeals/{appeal_id}/resolve", json={"status": "nip_aberta"}, headers=auth_headers_a
    )
    assert escalate_resp.status_code == 200

    events = _delivered_events()
    assert len(events) == 1
    assert events[0]["data"]["status"] == "nip_aberta"
    assert events[0]["data"]["previous_status"] == "indeferido"


# =====================================================================
# no_show_risk.high
# =====================================================================


async def _insert_past_appointment(admin_engine, tenant_id, patient_id, scheduled_at, status):
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.appointments (tenant_id, patient_id, scheduled_at, status) VALUES (:t, :p, :sched, :status)"),
            {"t": tenant_id, "p": patient_id, "sched": scheduled_at, "status": status},
        )


async def test_no_show_risk_high_dispatches_webhook_without_pii(client, auth_headers_a, admin_engine, tenant_a):
    await _create_webhook(client, auth_headers_a, event_types=["no_show_risk.high"])

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Faltoso Webhook"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    # Mesma técnica de test_no_show_risk.py: 3 faltas passadas na mesma
    # combinação dia-da-semana+período -> nova consulta nasce "alto".
    base_monday = date.today() - timedelta(days=date.today().weekday() + 7)
    for weeks_back in range(3):
        past_monday = base_monday - timedelta(weeks=weeks_back)
        scheduled = datetime.combine(past_monday, datetime.min.time(), tzinfo=timezone.utc).replace(hour=14)
        await _insert_past_appointment(admin_engine, tenant_a, patient_id, scheduled, "no_show")

    days_until_monday = (0 - date.today().weekday()) % 7
    next_monday = date.today() + timedelta(days=days_until_monday or 7)
    new_scheduled = datetime.combine(next_monday, datetime.min.time(), tzinfo=timezone.utc).replace(hour=14)

    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={"patient_id": patient_id, "scheduled_at": new_scheduled.isoformat()},
        headers=auth_headers_a,
    )
    assert appointment_resp.status_code == 201
    assert appointment_resp.json()["no_show_risk_level"] == "alto"

    events = _delivered_events()
    assert len(events) == 1
    assert events[0]["event_type"] == "no_show_risk.high"
    data = events[0]["data"]
    assert data["appointment_id"] == appointment_resp.json()["id"]
    assert data["patient_id"] == patient_id
    assert data["no_show_risk_score"] == 1.0
    raw_body = _FakeAsyncClient.calls[0]["content"].decode("utf-8")
    assert "Paciente Faltoso Webhook" not in raw_body


async def test_low_no_show_risk_does_not_dispatch(client, auth_headers_a):
    await _create_webhook(client, auth_headers_a, event_types=["no_show_risk.high"])

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Novo Webhook"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={"patient_id": patient_id, "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
        headers=auth_headers_a,
    )
    assert appointment_resp.status_code == 201
    assert appointment_resp.json()["no_show_risk_level"] == "indeterminado"
    assert _delivered_events() == []
