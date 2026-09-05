"""
tests/integration/test_audit_log.py

`core.audit_log` já existia como model desde o início do projeto, mas
por duas rodadas seguidas só teve metade do trabalho feito:
1. Uma rodada deu LEITURA (endpoint, repositório, esta suíte) — os
   testes ORIGINAIS abaixo inserem linha direto pelo `admin_engine`
   porque, até então, literalmente nada do produto escrevia ali.
2. Esta rodada (auditoria de acesso/LGPD) fecha o outro lado:
   `AuditLogRepository.record()` é chamado de dentro de
   patient_service.py, billing_service.py, user_service.py e
   denial_appeal_service.py — ver DECISÃO em cada um. A segunda seção
   deste arquivo (mais abaixo) prova isso fim-a-fim: aciona o endpoint
   HTTP de verdade (POST /patients, POST /billing, etc.) e confere que
   uma linha aparece em GET /audit-log, sem tocar o `admin_engine`.
"""
import uuid as uuid_module

from sqlalchemy import text


async def _insert_audit_log(
    admin_engine,
    *,
    tenant_id: str,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    actor_user_id: str | None = None,
):
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO core.audit_log (tenant_id, actor_user_id, action, entity_type, entity_id, diff)
                VALUES (:tenant_id, :actor_user_id, :action, :entity_type, :entity_id, :diff)
                """
            ),
            {
                "tenant_id": tenant_id,
                "actor_user_id": actor_user_id,
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


async def test_audit_log_resolves_actor_name(client, admin_engine, tenant_a, auth_headers_a):
    from tests.conftest import _insert_user

    actor = await _insert_user(admin_engine, tenant_id=tenant_a, email="renata.alves@audit-log-test.com", role="admin")
    await _insert_audit_log(
        admin_engine, tenant_id=tenant_a, action="update", entity_type="contract", actor_user_id=actor["id"]
    )
    # Ação disparada pelo próprio sistema, sem usuário logado.
    await _insert_audit_log(admin_engine, tenant_id=tenant_a, action="create", entity_type="billing")

    response = await client.get("/api/v1/audit-log", headers=auth_headers_a)
    assert response.status_code == 200
    items = {i["entity_type"]: i for i in response.json()["items"]}
    assert items["contract"]["actor_name"] == "renata.alves"
    assert items["billing"]["actor_user_id"] is None
    assert items["billing"]["actor_name"] is None


# =====================================================================
# Escrita de verdade — rodada de auditoria de acesso/LGPD. Diferente dos
# testes acima (que inserem a linha direto no banco para testar só a
# LEITURA), estes acionam o endpoint HTTP real e conferem que a
# mutação gerou uma linha em core.audit_log sozinha, sem `_insert_audit_log`.
# =====================================================================
from tests.integration.test_denial_appeals import _create_appeal, _create_billing, _create_insurance_plan  # noqa: E402


def _find_entry(items: list[dict], *, entity_type: str, action: str) -> dict | None:
    for item in items:
        if item["entity_type"] == entity_type and item["action"] == action:
            return item
    return None


async def test_create_patient_writes_audit_log(client, auth_headers_a, owner_a):
    resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Auditado"}, headers=auth_headers_a)
    assert resp.status_code == 201
    patient_id = resp.json()["id"]

    audit_resp = await client.get("/api/v1/audit-log", headers=auth_headers_a)
    entry = _find_entry(audit_resp.json()["items"], entity_type="patient", action="created")
    assert entry is not None
    assert entry["entity_id"] == patient_id
    assert entry["actor_name"] == owner_a["email"].split("@")[0]
    # Nunca duplica CPF/nome do paciente no audit log — ver DECISÃO em
    # AuditLogRepository.record.
    assert entry["diff"] is None


async def test_create_billing_writes_audit_log(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, auth_headers_a, plan_id)

    audit_resp = await client.get("/api/v1/audit-log", headers=auth_headers_a)
    entry = _find_entry(audit_resp.json()["items"], entity_type="billing", action="created")
    assert entry is not None
    assert entry["entity_id"] == billing_id


async def test_settle_billing_writes_audit_log_with_status_diff(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, auth_headers_a, plan_id)

    settle_resp = await client.post(
        f"/api/v1/billing/{billing_id}/settle", json={"received_value": 140.0}, headers=auth_headers_a
    )
    assert settle_resp.status_code == 200

    audit_resp = await client.get("/api/v1/audit-log", headers=auth_headers_a)
    entry = _find_entry(audit_resp.json()["items"], entity_type="billing", action="settled")
    assert entry is not None
    assert entry["entity_id"] == billing_id
    assert entry["diff"]["status"]["after"] == "paid"
    # Nunca duplica o valor financeiro no diff — só a transição de status.
    assert "received_value" not in entry["diff"]


async def test_create_user_writes_audit_log(client, auth_headers_a):
    resp = await client.post(
        "/api/v1/users",
        json={"email": "auditado@clinica-a.com", "full_name": "Usuário Auditado", "role": "atendimento"},
        headers=auth_headers_a,
    )
    assert resp.status_code == 201
    user_id = resp.json()["id"]

    audit_resp = await client.get("/api/v1/audit-log", headers=auth_headers_a)
    entry = _find_entry(audit_resp.json()["items"], entity_type="user", action="created")
    assert entry is not None
    assert entry["entity_id"] == user_id
    assert entry["diff"] == {"role": "atendimento"}


async def test_update_user_role_writes_audit_log_with_diff(client, admin_engine, tenant_a, auth_headers_a):
    from tests.conftest import _insert_user

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="promovido@clinica-a.com", role="atendimento")

    resp = await client.patch(f"/api/v1/users/{user['id']}", json={"role": "financeiro"}, headers=auth_headers_a)
    assert resp.status_code == 200

    audit_resp = await client.get("/api/v1/audit-log", headers=auth_headers_a)
    entry = _find_entry(audit_resp.json()["items"], entity_type="user", action="updated")
    assert entry is not None
    assert entry["entity_id"] == user["id"]
    assert entry["diff"]["role"] == {"before": "atendimento", "after": "financeiro"}


async def test_update_user_without_access_change_does_not_write_audit_log(client, admin_engine, tenant_a, auth_headers_a):
    """Trocar só o nome (sem mexer em papel/status ativo) não é um evento
    de CONTROLE DE ACESSO — não deveria gerar linha de auditoria, ver
    DECISÃO em UserService.update_user."""
    from tests.conftest import _insert_user

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="so.nome@clinica-a.com", role="atendimento")

    resp = await client.patch(f"/api/v1/users/{user['id']}", json={"full_name": "Nome Novo"}, headers=auth_headers_a)
    assert resp.status_code == 200

    audit_resp = await client.get("/api/v1/audit-log", headers=auth_headers_a)
    entry = _find_entry(audit_resp.json()["items"], entity_type="user", action="updated")
    assert entry is None


async def test_admin_reset_password_writes_audit_log_without_password(client, admin_engine, tenant_a, auth_headers_a):
    from tests.conftest import _insert_user

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="resetado@clinica-a.com", role="atendimento")

    resp = await client.post(f"/api/v1/users/{user['id']}/reset-password", headers=auth_headers_a)
    assert resp.status_code == 200
    temp_password = resp.json()["temporary_password"]

    audit_resp = await client.get("/api/v1/audit-log", headers=auth_headers_a)
    entry = _find_entry(audit_resp.json()["items"], entity_type="user", action="password_reset")
    assert entry is not None
    assert entry["entity_id"] == user["id"]
    assert entry["diff"] is None
    # A senha temporária não aparece em NENHUM campo do registro de auditoria.
    assert temp_password not in str(audit_resp.json())


async def test_denial_appeal_lifecycle_writes_audit_log_at_each_step(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, auth_headers_a, plan_id)
    appeal_id = await _create_appeal(client, auth_headers_a, billing_id)

    file_resp = await client.post(f"/api/v1/denial-appeals/{appeal_id}/file", json={}, headers=auth_headers_a)
    assert file_resp.status_code == 200

    resolve_resp = await client.post(
        f"/api/v1/denial-appeals/{appeal_id}/resolve", json={"status": "deferido"}, headers=auth_headers_a
    )
    assert resolve_resp.status_code == 200

    audit_resp = await client.get(
        "/api/v1/audit-log", params={"entity_type": "denial_appeal", "limit": 200}, headers=auth_headers_a
    )
    items = [i for i in audit_resp.json()["items"] if i["entity_id"] == appeal_id]
    actions = {i["action"] for i in items}
    assert actions == {"created", "filed", "resolved"}

    resolved_entry = next(i for i in items if i["action"] == "resolved")
    assert resolved_entry["diff"]["status"] == {"before": "protocolado", "after": "deferido"}
