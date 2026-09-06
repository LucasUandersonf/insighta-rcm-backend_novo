"""
tests/integration/test_announcements.py

Central de Notificações (sino de novidades/changelog) — ver DECISÃO
completa em app/sql/023_announcements_and_support.sql. Não existe
endpoint HTTP de criação (quem publica é a equipe da plataforma, via
app/scripts/publish_announcement.py) — os testes inserem a linha direto
pelo `admin_engine`, mesmo padrão já usado em test_audit_log.py antes de
existir qualquer fluxo de escrita real.
"""
import uuid as uuid_module

from sqlalchemy import text


async def _insert_announcement(admin_engine, *, title="Novidade de teste", body="Corpo da novidade.") -> str:
    announcement_id = str(uuid_module.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.platform_announcements (id, title, body) VALUES (:id, :title, :body)"),
            {"id": announcement_id, "title": title, "body": body},
        )
    return announcement_id


async def test_list_announcements_starts_unread(client, auth_headers_a, admin_engine):
    await _insert_announcement(admin_engine, title="Alerta de risco em tempo real")

    resp = await client.get("/api/v1/announcements", headers=auth_headers_a)
    assert resp.status_code == 200
    body = resp.json()
    assert body["unread_count"] == 1
    assert body["items"][0]["title"] == "Alerta de risco em tempo real"
    assert body["items"][0]["is_read"] is False


async def test_mark_announcement_read_decreases_unread_count(client, auth_headers_a, admin_engine):
    announcement_id = await _insert_announcement(admin_engine)

    mark_resp = await client.post(f"/api/v1/announcements/{announcement_id}/read", headers=auth_headers_a)
    assert mark_resp.status_code == 204

    list_resp = await client.get("/api/v1/announcements", headers=auth_headers_a)
    body = list_resp.json()
    assert body["unread_count"] == 0
    assert body["items"][0]["is_read"] is True


async def test_mark_announcement_read_twice_is_a_no_op(client, auth_headers_a, admin_engine):
    announcement_id = await _insert_announcement(admin_engine)

    first = await client.post(f"/api/v1/announcements/{announcement_id}/read", headers=auth_headers_a)
    assert first.status_code == 204
    second = await client.post(f"/api/v1/announcements/{announcement_id}/read", headers=auth_headers_a)
    assert second.status_code == 204


async def test_mark_unknown_announcement_404(client, auth_headers_a):
    resp = await client.post(
        "/api/v1/announcements/00000000-0000-0000-0000-000000000000/read", headers=auth_headers_a
    )
    assert resp.status_code == 404


async def test_announcement_is_shared_across_tenants(client, auth_headers_a, auth_headers_b, admin_engine):
    """A ÚNICA tabela do schema sem tenant_id/RLS — a mesma novidade
    aparece para as duas clínicas, sem duplicar a linha."""
    announcement_id = await _insert_announcement(admin_engine, title="Novidade global")

    resp_a = await client.get("/api/v1/announcements", headers=auth_headers_a)
    resp_b = await client.get("/api/v1/announcements", headers=auth_headers_b)

    ids_a = {i["id"] for i in resp_a.json()["items"]}
    ids_b = {i["id"] for i in resp_b.json()["items"]}
    assert announcement_id in ids_a
    assert announcement_id in ids_b


async def test_read_status_is_per_user_not_per_tenant(client, admin_engine, tenant_a, auth_headers_a):
    """Duas pessoas da MESMA clínica têm estados de leitura independentes
    — like a Slack/GitHub, não um "lido pra clínica inteira"."""
    from tests.conftest import _insert_user, _login

    announcement_id = await _insert_announcement(admin_engine)

    # owner (auth_headers_a) marca como lida.
    mark_resp = await client.post(f"/api/v1/announcements/{announcement_id}/read", headers=auth_headers_a)
    assert mark_resp.status_code == 204

    # colega do MESMO tenant, usuário DIFERENTE, ainda não leu.
    colleague = await _insert_user(admin_engine, tenant_id=tenant_a, email="colega@announcements-test.com", role="atendimento")
    token = await _login(client, colleague["email"], colleague["password"])
    colleague_headers = {"Authorization": f"Bearer {token}"}

    list_resp = await client.get("/api/v1/announcements", headers=colleague_headers)
    body = list_resp.json()
    assert body["unread_count"] == 1
    assert body["items"][0]["is_read"] is False
