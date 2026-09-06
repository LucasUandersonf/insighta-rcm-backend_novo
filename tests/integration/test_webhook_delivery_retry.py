"""
tests/integration/test_webhook_delivery_retry.py — fila de retentativa
para webhooks OUTBOUND (ver DECISÃO completa em
app/sql/028_webhook_delivery_queue.sql e
app/services/webhook_dispatch_service.py).

Mesma técnica de mock de test_webhook_subscriptions.py (substitui
httpx.AsyncClient inteiro) — reproduzida aqui em vez de importada, mesmo
padrão de helpers duplicados entre arquivos de teste já usado no resto
do projeto (_create_insurance_plan, etc.).
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.services import webhook_dispatch_service as dispatch_module
from app.worker.webhook_retry_job import run as run_webhook_retry_job


class _FakeResponse:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code


class _FakeAsyncClient:
    calls: list[dict] = []
    fail_urls: set[str] = set()

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, content=None, headers=None):
        self.__class__.calls.append({"url": url, "content": content, "headers": headers})
        if url in self.__class__.fail_urls:
            raise ConnectionError("destino inalcançável (simulado)")
        return _FakeResponse(200)


@pytest.fixture(autouse=True)
def _fake_webhook_delivery(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.fail_urls = set()
    monkeypatch.setattr(dispatch_module.httpx, "AsyncClient", _FakeAsyncClient)
    yield


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Unimed Nacional", normalized_key="unimed_nacional") -> str:
    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :n, :k)"),
            {"id": plan_id, "t": tenant_id, "n": display_name, "k": normalized_key},
        )
    return plan_id


async def _create_contract(admin_engine, tenant_id, plan_id, procedure_code, agreed_value=150.0):
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


async def _trigger_held_for_review_billing(client, auth_headers, admin_engine, tenant_id, *, procedure_code="99990001") -> None:
    """Dispara o único evento real ligado ao motor de webhooks hoje —
    ver DECISÃO em app/services/billing_service.py."""
    plan_id = await _create_insurance_plan(admin_engine, tenant_id)
    await _create_contract(admin_engine, tenant_id, plan_id, procedure_code=procedure_code, agreed_value=150.0)
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Retry"}, headers=auth_headers)
    patient_id = patient_resp.json()["id"]
    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": procedure_code,
            # cid_code OMITIDO -> alto risco -> held_for_review -> dispara o evento
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
    assert billing_resp.json()["status"] == "held_for_review"


async def _list_deliveries(client, auth_headers) -> list[dict]:
    resp = await client.get("/api/v1/integrations/webhooks/deliveries", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _backdate_next_attempt(admin_engine, delivery_id: str, when: datetime) -> None:
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("UPDATE core.webhook_delivery_queue SET next_attempt_at = :when WHERE id = :id"),
            {"when": when, "id": delivery_id},
        )


async def _set_attempt_count(admin_engine, delivery_id: str, count: int) -> None:
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("UPDATE core.webhook_delivery_queue SET attempt_count = :count WHERE id = :id"),
            {"count": count, "id": delivery_id},
        )


_FAILING_URL = "https://hooks.slack.com/services/fora-do-ar"


async def test_immediate_failure_enqueues_pending_retry(client, auth_headers_a, admin_engine, tenant_a):
    _FakeAsyncClient.fail_urls.add(_FAILING_URL)
    await client.post(
        "/api/v1/integrations/webhooks", json={"name": "Slack fora do ar", "url": _FAILING_URL}, headers=auth_headers_a
    )

    await _trigger_held_for_review_billing(client, auth_headers_a, admin_engine, tenant_a)

    deliveries = await _list_deliveries(client, auth_headers_a)
    assert len(deliveries) == 1
    entry = deliveries[0]
    assert entry["status"] == "pending"
    assert entry["attempt_count"] == 1
    assert entry["event_type"] == "billing.held_for_review"
    assert entry["last_error"]  # já registra o motivo da 1ª falha, não fica em branco até a 1ª retentativa
    next_attempt = datetime.fromisoformat(entry["next_attempt_at"])
    assert next_attempt > datetime.now(timezone.utc)


async def test_retry_job_delivers_successfully_once_destination_recovers(client, auth_headers_a, admin_engine, tenant_a):
    _FakeAsyncClient.fail_urls.add(_FAILING_URL)
    await client.post(
        "/api/v1/integrations/webhooks", json={"name": "Slack recuperado depois", "url": _FAILING_URL}, headers=auth_headers_a
    )
    await _trigger_held_for_review_billing(client, auth_headers_a, admin_engine, tenant_a)
    entry = (await _list_deliveries(client, auth_headers_a))[0]
    calls_before = len(_FakeAsyncClient.calls)

    # Destino "volta a funcionar" e o horário da retentativa já passou.
    _FakeAsyncClient.fail_urls.discard(_FAILING_URL)
    await _backdate_next_attempt(admin_engine, entry["id"], datetime.now(timezone.utc) - timedelta(seconds=1))

    await run_webhook_retry_job()

    assert len(_FakeAsyncClient.calls) == calls_before + 1
    updated = (await _list_deliveries(client, auth_headers_a))[0]
    assert updated["status"] == "delivered"


async def test_retry_job_reschedules_with_backoff_when_still_failing(client, auth_headers_a, admin_engine, tenant_a):
    _FakeAsyncClient.fail_urls.add(_FAILING_URL)
    await client.post(
        "/api/v1/integrations/webhooks", json={"name": "Slack sempre fora do ar", "url": _FAILING_URL}, headers=auth_headers_a
    )
    await _trigger_held_for_review_billing(client, auth_headers_a, admin_engine, tenant_a)
    entry = (await _list_deliveries(client, auth_headers_a))[0]

    await _backdate_next_attempt(admin_engine, entry["id"], datetime.now(timezone.utc) - timedelta(seconds=1))
    await run_webhook_retry_job()

    updated = (await _list_deliveries(client, auth_headers_a))[0]
    assert updated["status"] == "pending"
    assert updated["attempt_count"] == 2
    # Backoff da 3ª tentativa é de 5 minutos (bem maior que a margem de
    # execução do teste) — prova que não ficou "tentando de novo já".
    next_attempt = datetime.fromisoformat(updated["next_attempt_at"])
    assert next_attempt > datetime.now(timezone.utc) + timedelta(minutes=4)


async def test_retry_job_gives_up_after_max_attempts(client, auth_headers_a, admin_engine, tenant_a):
    _FakeAsyncClient.fail_urls.add(_FAILING_URL)
    await client.post(
        "/api/v1/integrations/webhooks", json={"name": "Slack irrecuperável", "url": _FAILING_URL}, headers=auth_headers_a
    )
    await _trigger_held_for_review_billing(client, auth_headers_a, admin_engine, tenant_a)
    entry = (await _list_deliveries(client, auth_headers_a))[0]

    # Simula que já é a última tentativa permitida (5 de 6, ver
    # _MAX_ATTEMPTS em webhook_dispatch_service.py) sem precisar rodar o
    # worker 5 vezes de verdade.
    await _set_attempt_count(admin_engine, entry["id"], 5)
    await _backdate_next_attempt(admin_engine, entry["id"], datetime.now(timezone.utc) - timedelta(seconds=1))

    await run_webhook_retry_job()

    updated = (await _list_deliveries(client, auth_headers_a))[0]
    assert updated["status"] == "failed"
    assert updated["attempt_count"] == 6


async def test_retry_job_marks_failed_when_subscription_deactivated(client, auth_headers_a, admin_engine, tenant_a):
    _FakeAsyncClient.fail_urls.add(_FAILING_URL)
    create_resp = await client.post(
        "/api/v1/integrations/webhooks", json={"name": "Será desativado", "url": _FAILING_URL}, headers=auth_headers_a
    )
    subscription_id = create_resp.json()["id"]
    await _trigger_held_for_review_billing(client, auth_headers_a, admin_engine, tenant_a)
    entry = (await _list_deliveries(client, auth_headers_a))[0]
    calls_before = len(_FakeAsyncClient.calls)

    await client.patch(f"/api/v1/integrations/webhooks/{subscription_id}", json={"active": False}, headers=auth_headers_a)
    await _backdate_next_attempt(admin_engine, entry["id"], datetime.now(timezone.utc) - timedelta(seconds=1))

    await run_webhook_retry_job()

    # Nenhuma tentativa HTTP nova — desistiu sem tentar entregar numa
    # assinatura que o próprio cliente desativou.
    assert len(_FakeAsyncClient.calls) == calls_before
    updated = (await _list_deliveries(client, auth_headers_a))[0]
    assert updated["status"] == "failed"
    assert "desativada" in updated["last_error"]


async def test_retry_queue_is_isolated_between_tenants(client, auth_headers_a, auth_headers_b, admin_engine, tenant_a, tenant_b):
    _FakeAsyncClient.fail_urls.add(_FAILING_URL)
    await client.post(
        "/api/v1/integrations/webhooks", json={"name": "Slack da Clínica A", "url": _FAILING_URL}, headers=auth_headers_a
    )
    await _trigger_held_for_review_billing(client, auth_headers_a, admin_engine, tenant_a)

    assert len(await _list_deliveries(client, auth_headers_a)) == 1
    assert await _list_deliveries(client, auth_headers_b) == []
