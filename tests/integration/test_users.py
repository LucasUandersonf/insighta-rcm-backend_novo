"""tests/integration/test_users.py — Gestão de Usuários (RBAC) e troca de
senha. Mesmo padrão de test_rbac.py: prova que o backend de fato barra
quem não devia, não só documenta a intenção."""
import pytest


async def test_owner_can_create_and_list_users(client, admin_engine, tenant_a, auth_headers_a):
    create_resp = await client.post(
        "/api/v1/users",
        json={"email": "nova.recepcao@clinica-a.com", "full_name": "Nova Recepção", "role": "atendimento"},
        headers=auth_headers_a,
    )
    assert create_resp.status_code == 201
    body = create_resp.json()
    assert body["role"] == "atendimento"
    assert body["must_change_password"] is True

    list_resp = await client.get("/api/v1/users", headers=auth_headers_a)
    assert list_resp.status_code == 200
    emails = [u["email"] for u in list_resp.json()]
    assert "nova.recepcao@clinica-a.com" in emails


async def test_atendimento_cannot_manage_users(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao@clinica-a.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/users",
        json={"email": "outro@clinica-a.com", "full_name": "Outro", "role": "atendimento"},
        headers=headers,
    )
    assert resp.status_code == 403


async def test_cannot_create_duplicate_email_in_same_tenant(client, auth_headers_a, owner_a):
    resp = await client.post(
        "/api/v1/users",
        json={"email": owner_a["email"], "full_name": "Duplicado", "role": "admin"},
        headers=auth_headers_a,
    )
    assert resp.status_code == 409


async def test_admin_cannot_deactivate_own_account(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    admin_user = await _insert_user(admin_engine, tenant_id=tenant_a, email="admin@clinica-a.com", role="admin")
    token = await _login(client, admin_user["email"], admin_user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.patch(f"/api/v1/users/{admin_user['id']}", json={"is_active": False}, headers=headers)
    assert resp.status_code == 400


async def test_owner_can_reset_password_and_user_can_login_with_temp_password(client, admin_engine, tenant_a, auth_headers_a):
    from tests.conftest import _insert_user

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="reset.me@clinica-a.com", role="atendimento")

    reset_resp = await client.post(f"/api/v1/users/{user['id']}/reset-password", headers=auth_headers_a)
    assert reset_resp.status_code == 200
    temp_password = reset_resp.json()["temporary_password"]
    assert len(temp_password) > 10

    login_resp = await client.post("/api/v1/auth/login", json={"email": user["email"], "password": temp_password})
    assert login_resp.status_code == 200


async def test_any_role_can_read_own_profile(client, admin_engine, tenant_a, auth_headers_a, owner_a):
    # owner lendo o próprio perfil — usado pela identificação de usuário
    # (avatar + nome) na barra superior do frontend.
    resp = await client.get("/api/v1/users/me", headers=auth_headers_a)
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == owner_a["email"]
    assert body["role"] == "owner"

    # papel sem permissão de gestão de usuários (atendimento) também lê o
    # PRÓPRIO perfil sem 403 — self-service, não "gestão de usuários".
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao.perfil@clinica-a.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])
    self_resp = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert self_resp.status_code == 200
    assert self_resp.json()["email"] == "recepcao.perfil@clinica-a.com"


async def test_user_can_change_own_password_but_not_with_wrong_current_password(client, auth_headers_a, owner_a):
    wrong_resp = await client.post(
        "/api/v1/users/me/change-password",
        json={"current_password": "senha-errada", "new_password": "nova-senha-123"},
        headers=auth_headers_a,
    )
    assert wrong_resp.status_code == 400

    ok_resp = await client.post(
        "/api/v1/users/me/change-password",
        json={"current_password": owner_a["password"], "new_password": "nova-senha-123"},
        headers=auth_headers_a,
    )
    assert ok_resp.status_code == 204

    login_resp = await client.post("/api/v1/auth/login", json={"email": owner_a["email"], "password": "nova-senha-123"})
    assert login_resp.status_code == 200
