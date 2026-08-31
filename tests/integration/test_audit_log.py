"""
tests/integration/test_audit_log.py

`core.audit_log` já existia como model antes desta mudança, mas sem
nenhum jeito de ler pela API. Aqui inserimos linhas direto pelo
`admin_engine` (não existe nenhum fluxo do produto que grava audit_log
através de um endpoint HTTP hoje) e conferimos paginação, filtros e RBAC
através do endpoint novo.
"""
import uuid as uuid_module

from sqlalchemy import text


async def _insert_audit_log(admin_engine, *, tenant_id: str, action: str, entity_type: str, entity_id: str | None = None):
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO core.audit_log (tenant_id, action, entity_type, entity_id, diff)
                VALUES (:tenant_id, :action, :entity_type, :entity_id, :diff)
                """
            ),
            {
                "tenant_id": tenant_id,
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id or str(uuid_module.uuid4()),
                "diff": '{"before": null, "after": {"status": "aberto"}}',
            },
        )


async def test_list_audit_log_paginated_envelope(client, auth_headers_a, admin_engine, tenant_a):
    for i in range(3):
        await _insert_audit_log(admin_engine, tenant_id=tenant_a, action="create", entity_type="patient")

    response = await client.get("/api/v1/audit-log", params={"limit": 2, "offset": 0}, headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert len(body["items"]) == 2

    page_2 = await client.get("/api/v1/audit-log", params={"limit": 2, "offset": 2}, headers=auth_headers_a)
    assert len(page_2.json()["items"]) == 1


async def test_list_audit_log_filters_by_entity_type_and_action(client, auth_headers_a, admin_engine, tenant_a):
    await _insert_audit_log(admin_engine, tenant_id=tenant_a, action="create", entity_type="patient")
    await _insert_audit_log(admin_engine, tenant_id=tenant_a, action="update", entity_type="contract")

    response = await client.get("/api/v1/audit-log", params={"entity_type": "contract"}, headers=auth_headers_a)
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["entity_type"] == "contract"

    response_action = await client.get("/api/v1/audit-log", params={"action": "create"}, headers=auth_headers_a)
    assert response_action.json()["total"] == 1


async def test_audit_log_isolated_by_tenant(client, auth_headers_a, auth_headers_b, admin_engine, tenant_a, tenant_b):
    await _insert_audit_log(admin_engine, tenant_id=tenant_a, action="create", entity_type="patient")
    await _insert_audit_log(admin_engine, tenant_id=tenant_b, action="create", entity_type="patient")

    response_a = await client.get("/api/v1/audit-log", headers=auth_headers_a)
    assert response_a.json()["total"] == 1

    response_b = await client.get("/api/v1/audit-log", headers=auth_headers_b)
    assert response_b.json()["total"] == 1


async def test_financeiro_cannot_read_audit_log(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="financeiro@audit-log-test.com", role="financeiro")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.get("/api/v1/audit-log", headers=headers)
    assert response.status_code == 403


async def test_auditor_can_read_audit_log(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    await _insert_audit_log(admin_engine, tenant_id=tenant_a, action="create", entity_type="patient")

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="auditor@audit-log-test.com", role="auditor")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.get("/api/v1/audit-log", headers=headers)
    assert response.status_code == 200
    assert response.json()["total"] == 1
