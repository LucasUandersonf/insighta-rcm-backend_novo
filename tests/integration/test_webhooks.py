"""
tests/integration/test_webhooks.py

Diferente de todo o resto: webhooks NÃO usam JWT (quem chama é a Meta,
não um usuário logado) — a autenticidade vem da assinatura HMAC do
corpo. Por isso o teste computa a assinatura manualmente sobre os BYTES
CRUS exatos que serão enviados (`content=`, não `json=` do httpx) — é
exatamente a mesma exigência documentada em
app/core/security.py:verify_meta_webhook_signature.
"""
import hashlib
import hmac
import json
import uuid

from sqlalchemy import text

_SECRET = "segredo-do-webhook-da-clinica-a"


async def _set_webhook_secret(admin_engine, tenant_id: str, secret: str = _SECRET) -> None:
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("UPDATE core.tenants SET meta_ads_webhook_secret = :s WHERE id = :t"),
            {"s": secret, "t": tenant_id},
        )


def _sign(raw_body: bytes, secret: str = _SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def test_verification_handshake_succeeds_with_correct_token(client, admin_engine, tenant_a):
    await _set_webhook_secret(admin_engine, tenant_a)

    response = await client.get(
        f"/api/v1/webhooks/meta-ads/{tenant_a}",
        params={"hub.mode": "subscribe", "hub.challenge": "12345", "hub.verify_token": _SECRET},
    )
    assert response.status_code == 200
    assert response.json() == 12345


async def test_verification_handshake_fails_with_wrong_token(client, admin_engine, tenant_a):
    await _set_webhook_secret(admin_engine, tenant_a)

    response = await client.get(
        f"/api/v1/webhooks/meta-ads/{tenant_a}",
        params={"hub.mode": "subscribe", "hub.challenge": "12345", "hub.verify_token": "token-errado"},
    )
    assert response.status_code == 403


async def test_receive_webhook_with_valid_signature_is_accepted(client, admin_engine, tenant_a):
    await _set_webhook_secret(admin_engine, tenant_a)

    raw_body = json.dumps({"entry": [{"id": "evt-abc-123", "time": 1234567890, "changes": []}]}).encode("utf-8")
    headers = {"X-Hub-Signature-256": _sign(raw_body), "Content-Type": "application/json"}

    response = await client.post(f"/api/v1/webhooks/meta-ads/{tenant_a}", content=raw_body, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "received"


async def test_receive_webhook_with_invalid_signature_is_rejected(client, admin_engine, tenant_a):
    await _set_webhook_secret(admin_engine, tenant_a)

    raw_body = json.dumps({"entry": [{"id": "evt-xyz", "time": 1234567890, "changes": []}]}).encode("utf-8")
    headers = {"X-Hub-Signature-256": "sha256=" + "0" * 64, "Content-Type": "application/json"}

    response = await client.post(f"/api/v1/webhooks/meta-ads/{tenant_a}", content=raw_body, headers=headers)
    assert response.status_code == 401


async def test_receive_webhook_without_configured_secret_is_rejected(client, tenant_a):
    # tenant_a aqui NÃO tem meta_ads_webhook_secret configurado (fixture não chamou _set_webhook_secret)
    raw_body = json.dumps({"entry": [{"id": "evt-no-secret", "time": 1, "changes": []}]}).encode("utf-8")
    headers = {"X-Hub-Signature-256": "sha256=" + "a" * 64, "Content-Type": "application/json"}

    response = await client.post(f"/api/v1/webhooks/meta-ads/{tenant_a}", content=raw_body, headers=headers)
    assert response.status_code == 401


async def test_duplicate_event_id_is_ignored_on_second_delivery(client, admin_engine, tenant_a):
    await _set_webhook_secret(admin_engine, tenant_a)

    raw_body = json.dumps({"entry": [{"id": "evt-duplicado", "time": 1234567890, "changes": []}]}).encode("utf-8")
    headers = {"X-Hub-Signature-256": _sign(raw_body), "Content-Type": "application/json"}

    first = await client.post(f"/api/v1/webhooks/meta-ads/{tenant_a}", content=raw_body, headers=headers)
    second = await client.post(f"/api/v1/webhooks/meta-ads/{tenant_a}", content=raw_body, headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == "received"
    assert second.json()["status"] == "duplicate_ignored"
