"""
tests/integration/test_platform_customer_success.py — painel interno de
Customer Success orientado a dados (ver DECISÃO completa em
app/sql/026_platform_customer_success.sql e app/sql/029_platform_users.sql).

Diferente de todo o resto do sistema: aqui NÃO há tenant_id no fluxo de
autenticação — é a equipe da Insighta, não um usuário de clínica. Por
isso a fixture `_platform_admin_user` insere direto em
core.platform_users (mesmo caminho de app/scripts/create_platform_user.py)
em vez de usar qualquer fixture de tenant/usuário para o LOGIN em si;
tenant_a/tenant_b só entram para provar que o relatório de fato
atravessa os dois ao mesmo tempo.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import text

from app.core.security import hash_password

_EMAIL = "equipe@insighta-rcm.com"
_PASSWORD = "senha-super-secreta-da-equipe"


@pytest_asyncio.fixture(autouse=True)
async def _platform_admin_user(admin_engine, clean_tables):
    # Depende de `clean_tables` (explícito no parâmetro) para garantir
    # que esta inserção acontece DEPOIS da limpeza entre testes — as
    # duas fixtures são autouse no mesmo escopo, e só uma dependência
    # explícita garante a ordem certa entre elas.
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.platform_users (id, email, hashed_password, full_name) "
                "VALUES (:id, :email, :hashed, 'Equipe de Teste')"
            ),
            {"id": str(uuid.uuid4()), "email": _EMAIL, "hashed": hash_password(_PASSWORD)},
        )
    yield


async def _platform_login(client, email: str = _EMAIL, password: str = _PASSWORD) -> str:
    resp = await client.post("/api/v1/platform/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _set_tenant_created_at(admin_engine, tenant_id: str, when: datetime) -> None:
    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE core.tenants SET created_at = :when WHERE id = :id"), {"when": when, "id": tenant_id})


async def _set_tenant_active(admin_engine, tenant_id: str, is_active: bool) -> None:
    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE core.tenants SET is_active = :active WHERE id = :id"), {"active": is_active, "id": tenant_id})


async def _insert_audit_events(admin_engine, tenant_id: str, count: int) -> None:
    async with admin_engine.begin() as conn:
        for _ in range(count):
            await conn.execute(
                text(
                    "INSERT INTO core.audit_log (tenant_id, action, entity_type, entity_id) "
                    "VALUES (:tenant_id, 'created', 'patient', :entity_id)"
                ),
                {"tenant_id": tenant_id, "entity_id": str(uuid.uuid4())},
            )


# =====================================================================
# Login — conta individual da equipe, nada a ver com tenant/usuário de
# clínica (ver DECISÃO em app/sql/029_platform_users.sql).
# =====================================================================


async def test_login_with_unknown_email_returns_401(client):
    resp = await client.post("/api/v1/platform/login", json={"email": "ninguem@insighta-rcm.com", "password": "qualquer-coisa"})
    assert resp.status_code == 401


async def test_login_with_wrong_password_returns_401(client):
    resp = await client.post("/api/v1/platform/login", json={"email": _EMAIL, "password": "senha-errada"})
    assert resp.status_code == 401


async def test_login_with_correct_password_returns_token(client):
    resp = await client.post("/api/v1/platform/login", json={"email": _EMAIL, "password": _PASSWORD})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"


async def test_login_updates_last_login_at_and_records_audit_log(client, admin_engine):
    await _platform_login(client)

    async with admin_engine.begin() as conn:
        row = (await conn.execute(text("SELECT last_login_at FROM core.platform_users WHERE email = :e"), {"e": _EMAIL})).first()
        assert row.last_login_at is not None
        log_row = (await conn.execute(text("SELECT action FROM core.platform_audit_log"))).first()
        assert log_row.action == "login"


async def test_deactivated_platform_user_cannot_login(client, admin_engine):
    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE core.platform_users SET is_active = false WHERE email = :e"), {"e": _EMAIL})

    resp = await client.post("/api/v1/platform/login", json={"email": _EMAIL, "password": _PASSWORD})
    assert resp.status_code == 401


# =====================================================================
# GET /platform/tenants-usage — só aceita o token do painel interno.
# =====================================================================


async def test_tenants_usage_requires_token(client):
    resp = await client.get("/api/v1/platform/tenants-usage")
    assert resp.status_code == 401


async def test_tenants_usage_rejects_clinic_jwt(client, auth_headers_a):
    """Um JWT de usuário de clínica válido (login normal) não tem
    `scope: platform_admin` — precisa ser recusado aqui, nunca aceito por
    engano só porque a assinatura bate (mesma chave secreta)."""
    resp = await client.get("/api/v1/platform/tenants-usage", headers=auth_headers_a)
    assert resp.status_code == 401


async def test_tenants_usage_lists_across_tenants(client, tenant_a, tenant_b):
    token = await _platform_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get("/api/v1/platform/tenants-usage", headers=headers)
    assert resp.status_code == 200
    tenant_ids = {item["tenant_id"] for item in resp.json()}
    # A prova de que este relatório é DE PROPÓSITO cross-tenant: as duas
    # clínicas de teste aparecem juntas na MESMA resposta, algo que
    # nenhum outro endpoint do sistema permite.
    assert tenant_a in tenant_ids
    assert tenant_b in tenant_ids


# =====================================================================
# Régua de engajamento (PlatformReportingService._classify_engagement).
# =====================================================================


async def test_brand_new_tenant_with_no_activity_is_classified_as_novo(client, tenant_a):
    token = await _platform_login(client)
    resp = await client.get("/api/v1/platform/tenants-usage", headers={"Authorization": f"Bearer {token}"})
    item = next(i for i in resp.json() if i["tenant_id"] == tenant_a)
    assert item["engagement_status"] == "novo"
    assert item["events_last_30d"] == 0


async def test_old_tenant_with_no_recent_activity_is_classified_as_risco(client, admin_engine, tenant_a):
    await _set_tenant_created_at(admin_engine, tenant_a, datetime.now(timezone.utc) - timedelta(days=30))

    token = await _platform_login(client)
    resp = await client.get("/api/v1/platform/tenants-usage", headers={"Authorization": f"Bearer {token}"})
    item = next(i for i in resp.json() if i["tenant_id"] == tenant_a)
    assert item["engagement_status"] == "risco"
    assert item["last_activity_at"] is None
    assert item["days_since_last_activity"] is None


async def test_old_tenant_with_low_activity_is_classified_as_atencao(client, admin_engine, tenant_a):
    await _set_tenant_created_at(admin_engine, tenant_a, datetime.now(timezone.utc) - timedelta(days=30))
    await _insert_audit_events(admin_engine, tenant_a, count=2)

    token = await _platform_login(client)
    resp = await client.get("/api/v1/platform/tenants-usage", headers={"Authorization": f"Bearer {token}"})
    item = next(i for i in resp.json() if i["tenant_id"] == tenant_a)
    assert item["engagement_status"] == "atencao"
    assert item["events_last_30d"] == 2
    assert item["days_since_last_activity"] == 0


async def test_old_tenant_with_high_activity_is_classified_as_engajado(client, admin_engine, tenant_a):
    await _set_tenant_created_at(admin_engine, tenant_a, datetime.now(timezone.utc) - timedelta(days=30))
    await _insert_audit_events(admin_engine, tenant_a, count=6)

    token = await _platform_login(client)
    resp = await client.get("/api/v1/platform/tenants-usage", headers={"Authorization": f"Bearer {token}"})
    item = next(i for i in resp.json() if i["tenant_id"] == tenant_a)
    assert item["engagement_status"] == "engajado"
    assert item["events_last_30d"] == 6


async def test_deactivated_tenant_is_classified_as_inativo_even_with_activity(client, admin_engine, tenant_a):
    await _set_tenant_created_at(admin_engine, tenant_a, datetime.now(timezone.utc) - timedelta(days=30))
    await _insert_audit_events(admin_engine, tenant_a, count=10)
    await _set_tenant_active(admin_engine, tenant_a, False)

    token = await _platform_login(client)
    resp = await client.get("/api/v1/platform/tenants-usage", headers={"Authorization": f"Bearer {token}"})
    item = next(i for i in resp.json() if i["tenant_id"] == tenant_a)
    assert item["engagement_status"] == "inativo"


async def test_active_users_count_reflects_only_active_users(client, admin_engine, tenant_a, owner_a):
    from tests.conftest import _insert_user

    await _insert_user(admin_engine, tenant_id=tenant_a, email="segundo@clinica-a.com", role="atendimento")
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.users (id, tenant_id, email, hashed_password, full_name, role, is_active) "
                 "VALUES (:id, :tenant_id, 'desativado@clinica-a.com', 'x', 'Desativado', 'atendimento', false)"),
            {"id": str(uuid.uuid4()), "tenant_id": tenant_a},
        )

    token = await _platform_login(client)
    resp = await client.get("/api/v1/platform/tenants-usage", headers={"Authorization": f"Bearer {token}"})
    item = next(i for i in resp.json() if i["tenant_id"] == tenant_a)
    # owner_a + segundo (ambos ativos) = 2; o desativado não conta.
    assert item["active_users"] == 2


# =====================================================================
# GET /platform/audit-log — "histórico de quem fez o quê"
# (ver DECISÃO em app/sql/029_platform_users.sql).
# =====================================================================


async def test_audit_log_requires_token(client):
    resp = await client.get("/api/v1/platform/audit-log")
    assert resp.status_code == 401


async def test_audit_log_records_login_with_actor_email(client):
    token = await _platform_login(client)

    resp = await client.get("/api/v1/platform/audit-log", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 1
    assert entries[0]["action"] == "login"
    assert entries[0]["actor_email"] == _EMAIL


async def test_audit_log_records_alerts_run_action(client):
    token = await _platform_login(client)
    await client.post("/api/v1/platform/alerts/run", headers={"Authorization": f"Bearer {token}"})

    resp = await client.get("/api/v1/platform/audit-log", headers={"Authorization": f"Bearer {token}"})
    actions = [e["action"] for e in resp.json()]
    # "login" (deste teste) + "alerts_run" — mais recente primeiro.
    assert actions[0] == "alerts_run"
    assert "login" in actions
