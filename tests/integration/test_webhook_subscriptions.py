"""
tests/integration/test_webhook_subscriptions.py — segunda metade de
"Integrações genéricas (webhooks/API)": sentido OUTBOUND.

Diferente de test_webhooks.py (que prova que a plataforma VERIFICA um
webhook recebido da Meta), este arquivo prova o lado inverso: a
plataforma EMITE um webhook assinado quando um evento de negócio
acontece. A fronteira de rede real (o servidor do cliente) é mockada —
tudo o mais (RLS, RBAC, HMAC, o gatilho de negócio em BillingService)
roda de verdade, mesma técnica de test_integrations.py (mocka só o S3).
"""
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.services import webhook_dispatch_service as dispatch_module


class _FakeResponse:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code


class _FakeAsyncClient:
    """Substitui httpx.AsyncClient inteiro (não só `.post`) porque
    dispatch_event() o usa como context manager (`async with`) — mesma
    necessidade de simular __aenter__/__aexit__ que um teste de
    WhatsAppClient não tem (lá se mocka o método do client, não o
    transporte HTTP em si)."""

    calls: list[dict] = []
    fail_urls: set[str] = set()

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, content=None, headers=None):
        if url in _FakeAsyncClient.fail_urls:
            raise ConnectionError("destino inalcançável (simulado)")
        _FakeAsyncClient.calls.append({"url": url, "content": content, "headers": headers})
        return _FakeResponse(200)


@pytest.fixture(autouse=True)
def _fake_webhook_delivery(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.fail_urls = set()
    monkeypatch.setattr(dispatch_module.httpx, "AsyncClient", _FakeAsyncClient)
    yield


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Unimed Nacional", normalized_key="unimed_nacional") -> str:
    import uuid

    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :n, :k)"),
            {"id": plan_id, "t": tenant_id, "n": display_name, "k": normalized_key},
        )
    return plan_id


async def _create_contract(admin_engine, tenant_id, plan_id, procedure_code, agreed_value=150.0):
    import uuid

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


# =====================================================================
# CRUD — GET/POST/PATCH/DELETE /integrations/webhooks
# =====================================================================


async def test_owner_can_create_list_update_and_delete_webhook(client, auth_headers_a):
    create_resp = await client.post(
        "/api/v1/integrations/webhooks",
        json={"name": "Slack da Clínica A", "url": "https://hooks.slack.com/services/xyz", "event_types": ["billing.held_for_review"]},
        headers=auth_headers_a,
    )
    assert create_resp.status_code == 201, create_resp.text
    body = create_resp.json()
    assert "secret" in body and len(body["secret"]) == 64
    subscription_id = body["id"]

    list_resp = await client.get("/api/v1/integrations/webhooks", headers=auth_headers_a)
    assert list_resp.status_code == 200
    assert all("secret" not in item for item in list_resp.json())  # nunca reexibe o segredo depois da criação
    assert any(item["id"] == subscription_id for item in list_resp.json())

    update_resp = await client.patch(
        f"/api/v1/integrations/webhooks/{subscription_id}", json={"active": False}, headers=auth_headers_a
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["active"] is False

    delete_resp = await client.delete(f"/api/v1/integrations/webhooks/{subscription_id}", headers=auth_headers_a)
    assert delete_resp.status_code == 204

    list_after = await client.get("/api/v1/integrations/webhooks", headers=auth_headers_a)
    assert list_after.json() == []


async def test_webhook_url_must_be_https(client, auth_headers_a):
    resp = await client.post(
        "/api/v1/integrations/webhooks",
        json={"name": "URL insegura", "url": "http://exemplo.com/webhook"},
        headers=auth_headers_a,
    )
    assert resp.status_code == 422


async def test_atendimento_cannot_manage_webhooks(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao3@clinica-a.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/integrations/webhooks", json={"name": "Tentativa indevida", "url": "https://exemplo.com"}, headers=headers
    )
    assert resp.status_code == 403


async def test_tenant_b_cannot_see_or_touch_tenant_a_webhooks(client, auth_headers_a, auth_headers_b):
    create_resp = await client.post(
        "/api/v1/integrations/webhooks", json={"name": "Webhook da Clínica A", "url": "https://exemplo-a.com"}, headers=auth_headers_a
    )
    subscription_id = create_resp.json()["id"]

    list_b = await client.get("/api/v1/integrations/webhooks", headers=auth_headers_b)
    assert list_b.json() == []

    # RLS esconde a linha de outro tenant -> 404, não 403 (mesmo padrão do resto do sistema)
    patch_b = await client.patch(
        f"/api/v1/integrations/webhooks/{subscription_id}", json={"active": False}, headers=auth_headers_b
    )
    assert patch_b.status_code == 404


# =====================================================================
# Disparo real: billing.held_for_review entregue (assinado) ao webhook.
# =====================================================================


async def test_billing_held_for_review_dispatches_signed_webhook(client, auth_headers_a, admin_engine, tenant_a):
    create_resp = await client.post(
        "/api/v1/integrations/webhooks",
        json={"name": "Slack da Clínica A", "url": "https://hooks.slack.com/services/xyz"},
        headers=auth_headers_a,
    )
    secret = create_resp.json()["secret"]

    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_contract(admin_engine, tenant_a, plan_id, procedure_code="10101012", agreed_value=150.0)

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Glosa Webhook"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
            # cid_code OMITIDO -> alto risco -> held_for_review -> dispara o evento
        },
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]

    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 150.0},
        headers=auth_headers_a,
    )
    assert billing_resp.status_code == 201
    assert billing_resp.json()["status"] == "held_for_review"

    assert len(_FakeAsyncClient.calls) == 1
    call = _FakeAsyncClient.calls[0]
    assert call["url"] == "https://hooks.slack.com/services/xyz"

    raw_body = call["content"]
    expected_signature = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    assert call["headers"]["X-Insighta-Signature"] == expected_signature

    delivered = json.loads(raw_body)
    assert delivered["event_type"] == "billing.held_for_review"
    assert delivered["data"]["billing_id"] == billing_resp.json()["id"]
    assert delivered["data"]["denial_risk_level"] == "high"
    # Nunca PII/dado clínico no corpo do evento (ver DECISÃO em webhook_dispatch_service.py)
    assert "Paciente Glosa Webhook" not in raw_body.decode("utf-8")


async def test_clean_billing_does_not_dispatch_any_webhook(client, auth_headers_a, admin_engine, tenant_a):
    await client.post(
        "/api/v1/integrations/webhooks", json={"name": "Slack da Clínica A", "url": "https://hooks.slack.com/services/xyz"},
        headers=auth_headers_a,
    )
    plan_id = await _create_insurance_plan(admin_engine, tenant_a, display_name="Bradesco", normalized_key="bradesco")
    await _create_contract(admin_engine, tenant_a, plan_id, procedure_code="88888888", agreed_value=150.0)

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Limpo Webhook"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "88888888",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]

    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 150.0},
        headers=auth_headers_a,
    )
    assert billing_resp.status_code == 201
    assert billing_resp.json()["status"] == "pending"
    assert _FakeAsyncClient.calls == []


async def test_inactive_subscription_is_never_called(client, auth_headers_a, admin_engine, tenant_a):
    create_resp = await client.post(
        "/api/v1/integrations/webhooks",
        json={"name": "Slack desativado", "url": "https://hooks.slack.com/services/inativo", "active": False},
        headers=auth_headers_a,
    )
    subscription_id = create_resp.json()["id"]
    assert (await client.get("/api/v1/integrations/webhooks", headers=auth_headers_a)).json()[0]["active"] is False

    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_contract(admin_engine, tenant_a, plan_id, procedure_code="77777777", agreed_value=150.0)
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente X"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "77777777",
        },
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]

    await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 150.0},
        headers=auth_headers_a,
    )
    assert _FakeAsyncClient.calls == []
    assert subscription_id  # sanity: a assinatura existe, só não foi chamada


async def test_delivery_failure_never_breaks_billing_creation(client, auth_headers_a, admin_engine, tenant_a):
    """dispatch_event() nunca deve propagar falha de rede — criar o
    faturamento tem que dar certo mesmo com o destino fora do ar (ver
    DECISÃO em webhook_dispatch_service.py)."""
    _FakeAsyncClient.fail_urls.add("https://hooks.slack.com/services/fora-do-ar")
    await client.post(
        "/api/v1/integrations/webhooks",
        json={"name": "Slack fora do ar", "url": "https://hooks.slack.com/services/fora-do-ar"},
        headers=auth_headers_a,
    )

    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_contract(admin_engine, tenant_a, plan_id, procedure_code="66666666", agreed_value=150.0)
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Y"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "66666666",
        },
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]

    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 150.0},
        headers=auth_headers_a,
    )
    assert billing_resp.status_code == 201
    assert billing_resp.json()["status"] == "held_for_review"
