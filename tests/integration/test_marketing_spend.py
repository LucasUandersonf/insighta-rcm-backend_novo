"""
tests/integration/test_marketing_spend.py

Achado do Dossiê Insighta RCM — Onda 2 do Plano de Ação: fecha o
pipeline de escrita de core.marketing_spend. Ponta a ponta via
POST/GET/DELETE /marketing-spend, e prova que o gasto lançado aparece
de fato em ReportingRepository.marketing_spend_total (o consumidor real
que já existia, mas nunca tinha dado pra ler).
"""
from datetime import date

from tests.conftest import _insert_user, _login


async def _create_marketing_spend(client, auth_headers, *, source="meta_ads", campaign_id="camp-1", amount_spent=500.0, spend_date="2026-01-10"):
    resp = await client.post(
        "/api/v1/marketing-spend",
        json={"source": source, "campaign_id": campaign_id, "amount_spent": amount_spent, "spend_date": spend_date},
        headers=auth_headers,
    )
    return resp


async def test_creates_marketing_spend(client, auth_headers_a):
    resp = await client.post(
        "/api/v1/marketing-spend",
        json={
            "source": "meta_ads",
            "campaign_id": "camp-1",
            "campaign_name": "Campanha Teste",
            "amount_spent": 500.0,
            "spend_date": "2026-01-10",
            "impressions": 10000,
            "clicks": 250,
        },
        headers=auth_headers_a,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source"] == "meta_ads"
    assert body["campaign_name"] == "Campanha Teste"
    assert body["amount_spent"] == 500.0
    assert body["impressions"] == 10000


async def test_rejects_unknown_source(client, auth_headers_a):
    resp = await client.post(
        "/api/v1/marketing-spend",
        json={"source": "tiktok_ads", "campaign_id": "camp-1", "amount_spent": 100.0, "spend_date": "2026-01-10"},
        headers=auth_headers_a,
    )
    assert resp.status_code == 422


async def test_duplicate_campaign_and_date_returns_409(client, auth_headers_a):
    first = await _create_marketing_spend(client, auth_headers_a, campaign_id="camp-dup", spend_date="2026-01-10")
    assert first.status_code == 201, first.text

    second = await _create_marketing_spend(client, auth_headers_a, campaign_id="camp-dup", spend_date="2026-01-10")
    assert second.status_code == 409


async def test_lists_marketing_spend_paginated(client, auth_headers_a):
    await _create_marketing_spend(client, auth_headers_a, campaign_id="camp-list-1", spend_date="2026-01-10")
    await _create_marketing_spend(client, auth_headers_a, campaign_id="camp-list-2", spend_date="2026-01-11")

    resp = await client.get("/api/v1/marketing-spend?limit=1&offset=0", headers=auth_headers_a)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1


async def test_deletes_marketing_spend(client, auth_headers_a):
    create_resp = await _create_marketing_spend(client, auth_headers_a, campaign_id="camp-delete")
    marketing_spend_id = create_resp.json()["id"]

    delete_resp = await client.delete(f"/api/v1/marketing-spend/{marketing_spend_id}", headers=auth_headers_a)
    assert delete_resp.status_code == 204

    list_resp = await client.get("/api/v1/marketing-spend", headers=auth_headers_a)
    assert list_resp.json()["total"] == 0


async def test_delete_nonexistent_returns_404(client, auth_headers_a):
    import uuid

    resp = await client.delete(f"/api/v1/marketing-spend/{uuid.uuid4()}", headers=auth_headers_a)
    assert resp.status_code == 404


async def test_atendimento_role_cannot_create_marketing_spend(client, admin_engine, tenant_a):
    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao@marketing.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/marketing-spend",
        json={"source": "meta_ads", "campaign_id": "camp-1", "amount_spent": 100.0, "spend_date": "2026-01-10"},
        headers=headers,
    )
    assert resp.status_code == 403


async def test_marketing_spend_is_isolated_between_tenants(client, auth_headers_a, auth_headers_b):
    await _create_marketing_spend(client, auth_headers_a, campaign_id="camp-tenant-a")

    resp_b = await client.get("/api/v1/marketing-spend", headers=auth_headers_b)
    assert resp_b.status_code == 200
    assert resp_b.json()["total"] == 0


async def test_marketing_spend_total_reads_what_was_written(client, auth_headers_a):
    """O achado do Dossiê: a query de leitura
    (ReportingRepository.marketing_performance_by_campaign, usada em
    GET /analytics/marketing-channels) já existia e funcionava — só
    nunca tinha dado pra ler. Prova que o gasto lançado aqui aparece de
    fato no resumo de marketing por campanha."""
    today = date.today().isoformat()
    await _create_marketing_spend(client, auth_headers_a, campaign_id="camp-roi", amount_spent=750.0, spend_date=today)

    resp = await client.get(
        f"/api/v1/analytics/marketing-channels?date_from={today}&date_to={today}", headers=auth_headers_a
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    campaign = next(i for i in items if i["campaign_id"] == "camp-roi")
    assert campaign["spend"] == 750.0
