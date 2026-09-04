"""tests/integration/test_auth_register_and_password_reset.py — cadastro
público (self-signup) e recuperação de senha self-service. Mesmo padrão
de test_users.py: prova o comportamento fim a fim contra um Postgres
real (RLS incluso), não só documenta a intenção.
"""
import re

import pytest


def _unique_cnpj() -> str:
    # BUG CORRIGIDO: `uuid.uuid4().hex[:14]` inclui letras a-f — a
    # validação de CNPJ conta só dígitos (ver
    # RegisterRequest.validate_cnpj_format), então a maioria das strings
    # geradas antes tinha menos de 14 dígitos e falhava com 422 antes
    # mesmo de chegar na regra de negócio que este arquivo testa. CNPJ
    # exige exatamente 14 dígitos — usa só o componente numérico (int) de
    # um uuid4, garantindo um valor totalmente numérico e ainda
    # praticamente único entre execuções.
    import uuid

    return str(uuid.uuid4().int)[:14]


async def test_register_creates_tenant_and_owner_and_returns_token(client):
    payload = {
        "trade_name": "Clínica Nova",
        "cnpj": _unique_cnpj(),
        "plan_tier": "professional",
        "owner_name": "Maria Proprietária",
        "email": "maria@clinica-nova.com",
        "password": "senha-forte-123",
    }
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"

    # O token emitido já autentica como owner da clínica recém-criada —
    # GET /tenant confirma os dados persistidos (plan_tier, trade_name).
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    tenant_resp = await client.get("/api/v1/tenant", headers=headers)
    assert tenant_resp.status_code == 200
    tenant_body = tenant_resp.json()
    assert tenant_body["trade_name"] == "Clínica Nova"
    assert tenant_body["plan_tier"] == "professional"
    assert tenant_body["is_active"] is True


async def test_register_rejects_duplicate_cnpj(client):
    cnpj = _unique_cnpj()
    payload = {
        "trade_name": "Clínica Um",
        "cnpj": cnpj,
        "owner_name": "Dono Um",
        "email": "dono1@clinica.com",
        "password": "senha-forte-123",
    }
    first = await client.post("/api/v1/auth/register", json=payload)
    assert first.status_code == 201

    payload["trade_name"] = "Clínica Dois (mesmo CNPJ)"
    payload["email"] = "dono2@clinica.com"
    second = await client.post("/api/v1/auth/register", json=payload)
    assert second.status_code == 409


async def test_register_rejects_weak_password(client):
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "trade_name": "Clínica Fraca",
            "cnpj": _unique_cnpj(),
            "owner_name": "Dono",
            "email": "dono@clinica-fraca.com",
            "password": "123",
        },
    )
    assert resp.status_code == 422


async def test_password_reset_request_always_returns_202(client, owner_a):
    # E-mail existente...
    existing = await client.post("/api/v1/auth/password-reset/request", json={"email": owner_a["email"]})
    assert existing.status_code == 202

    # ...e e-mail inexistente devolvem exatamente a mesma resposta —
    # anti-enumeração (ver DECISÃO em AuthService.request_password_reset).
    missing = await client.post("/api/v1/auth/password-reset/request", json={"email": "ninguem@nao-existe.com"})
    assert missing.status_code == 202


async def test_password_reset_confirm_rejects_invalid_token(client):
    resp = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": "token-que-nao-existe", "new_password": "outra-senha-123"},
    )
    assert resp.status_code == 400


async def test_password_reset_full_round_trip(client, owner_a, monkeypatch):
    """Intercepta o envio de e-mail (EmailClient.send) para capturar o
    token em texto puro — nunca exposto por nenhum endpoint (só o hash
    fica no banco, ver PasswordResetTokenRepository), então o único jeito
    de testar o fluxo de ponta a ponta é capturá-lo no ponto de "envio"."""
    captured: dict[str, str] = {}

    async def fake_send(self, *, to_email, subject, text_body, html_body=None):
        captured["text_body"] = text_body

    monkeypatch.setattr("app.services.email_client.EmailClient.send", fake_send)

    request_resp = await client.post("/api/v1/auth/password-reset/request", json={"email": owner_a["email"]})
    assert request_resp.status_code == 202
    assert "text_body" in captured

    match = re.search(r"token=([\w-]+)", captured["text_body"])
    assert match, "link de redefinição não encontrado no corpo do e-mail capturado"
    raw_token = match.group(1)

    confirm_resp = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": raw_token, "new_password": "senha-recem-definida-123"},
    )
    assert confirm_resp.status_code == 204

    # Login com a senha antiga não funciona mais...
    old_login = await client.post("/api/v1/auth/login", json={"email": owner_a["email"], "password": owner_a["password"]})
    assert old_login.status_code == 401

    # ...e com a nova senha funciona.
    new_login = await client.post("/api/v1/auth/login", json={"email": owner_a["email"], "password": "senha-recem-definida-123"})
    assert new_login.status_code == 200

    # Reusar o mesmo token (já consumido) deve falhar.
    reuse_resp = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": raw_token, "new_password": "mais-uma-senha-123"},
    )
    assert reuse_resp.status_code == 400
