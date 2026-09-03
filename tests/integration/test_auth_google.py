"""tests/integration/test_auth_google.py — "Continuar com Google"
(login + cadastro). Mesmo padrão dos demais arquivos de tests/integration/:
prova contra um Postgres real (RLS incluso), não só documenta a intenção.

Não temos como gerar um ID token de verdade assinado pelo Google nos
testes (exigiria uma conta Google real) — por isso todo teste aqui
substitui `verify_google_id_token` (o único ponto que fala com a rede)
por um fake determinístico via monkeypatch. É exatamente a fronteira
certa para dublar: tudo o que vem DEPOIS da verificação (resolução de
tenant, criação de conta, emissão de JWT) roda de verdade, sem mock.
"""
import uuid

import pytest

from app.services.google_oauth_client import GoogleUserInfo


def _unique_cnpj() -> str:
    return uuid.uuid4().hex[:14]


def _fake_google_user(email: str, name: str = "Usuário Google"):
    async def fake_verify(credential: str) -> GoogleUserInfo:
        assert credential == "fake-credential"  # garante que o token realmente chegou até aqui
        return GoogleUserInfo(email=email, name=name)

    return fake_verify


async def test_google_auth_signals_registration_for_unknown_email(client, monkeypatch):
    monkeypatch.setattr("app.services.auth_service.verify_google_id_token", _fake_google_user("novo@gmail.com", "Pessoa Nova"))

    resp = await client.post("/api/v1/auth/google", json={"credential": "fake-credential"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["needs_registration"] is True
    assert body["email"] == "novo@gmail.com"
    assert body["suggested_owner_name"] == "Pessoa Nova"
    assert body["access_token"] is None


async def test_google_auth_logs_in_existing_user(client, owner_a, monkeypatch):
    monkeypatch.setattr("app.services.auth_service.verify_google_id_token", _fake_google_user(owner_a["email"]))

    resp = await client.post("/api/v1/auth/google", json={"credential": "fake-credential"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["needs_registration"] is False

    headers = {"Authorization": f"Bearer {body['access_token']}"}
    me = await client.get("/api/v1/tenant", headers=headers)
    assert me.status_code == 200


async def test_google_auth_multi_tenant_requires_selection_then_logs_in(client, admin_engine, tenant_a, tenant_b, monkeypatch):
    from tests.conftest import _insert_user

    shared_email = "consultor@gmail.com"
    await _insert_user(admin_engine, tenant_id=tenant_a, email=shared_email, role="financeiro")
    await _insert_user(admin_engine, tenant_id=tenant_b, email=shared_email, role="auditor")
    monkeypatch.setattr("app.services.auth_service.verify_google_id_token", _fake_google_user(shared_email))

    ambiguous = await client.post("/api/v1/auth/google", json={"credential": "fake-credential"})
    assert ambiguous.status_code == 200
    ambiguous_body = ambiguous.json()
    assert ambiguous_body["requires_tenant_selection"] is True
    assert len(ambiguous_body["tenant_options"]) == 2

    chosen_tenant_id = ambiguous_body["tenant_options"][0]["tenant_id"]
    resolved = await client.post("/api/v1/auth/google", json={"credential": "fake-credential", "tenant_id": chosen_tenant_id})
    assert resolved.status_code == 200
    assert resolved.json()["access_token"]


async def test_register_with_google_credential_creates_tenant_and_authenticates(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.auth_service.verify_google_id_token", _fake_google_user("dona@gmail.com", "Dona da Clínica")
    )

    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "trade_name": "Clínica via Google",
            "cnpj": _unique_cnpj(),
            "plan_tier": "starter",
            "google_credential": "fake-credential",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["access_token"]

    headers = {"Authorization": f"Bearer {body['access_token']}"}
    tenant_resp = await client.get("/api/v1/tenant", headers=headers)
    assert tenant_resp.status_code == 200
    assert tenant_resp.json()["trade_name"] == "Clínica via Google"


async def test_register_rejects_password_together_with_google_credential(client):
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "trade_name": "Clínica Confusa",
            "cnpj": _unique_cnpj(),
            "google_credential": "fake-credential",
            "password": "senha1234",
        },
    )
    assert resp.status_code == 422
