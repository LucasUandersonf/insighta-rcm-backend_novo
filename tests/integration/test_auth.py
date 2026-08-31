"""
tests/integration/test_auth.py

Valida o fluxo mais delicado da arquitetura: login funciona SOB RLS
(usando a função SECURITY DEFINER core.resolve_login), e o "ovo e
galinha" documentado em 002_auth_resolver.sql realmente se resolve.
"""
import uuid as uuid_module


async def test_login_with_correct_credentials_returns_token(client, owner_a):
    response = await client.post(
        "/api/v1/auth/login", json={"email": owner_a["email"], "password": owner_a["password"]}
    )
    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"


async def test_login_with_wrong_password_returns_generic_401(client, owner_a):
    response = await client.post("/api/v1/auth/login", json={"email": owner_a["email"], "password": "senha-errada"})
    assert response.status_code == 401
    assert "inválidos" in response.json()["detail"]


async def test_login_with_unknown_email_returns_same_generic_401(client, owner_a):
    """
    E-mail inexistente e senha errada devem devolver EXATAMENTE a mesma
    mensagem — diferenciar os dois permitiria enumerar e-mails válidos
    (ver DECISÃO em app/api/v1/endpoints/auth.py).
    """
    wrong_password_response = await client.post(
        "/api/v1/auth/login", json={"email": owner_a["email"], "password": "senha-errada"}
    )
    unknown_email_response = await client.post(
        "/api/v1/auth/login", json={"email": "ninguem@nunca-existiu.com", "password": "qualquer-coisa"}
    )
    assert wrong_password_response.status_code == unknown_email_response.status_code == 401
    assert wrong_password_response.json()["detail"] == unknown_email_response.json()["detail"]


async def test_protected_endpoint_without_token_is_rejected(client):
    response = await client.get("/api/v1/patients")
    assert response.status_code == 401


async def test_protected_endpoint_with_garbage_token_is_rejected(client):
    response = await client.get("/api/v1/patients", headers={"Authorization": "Bearer token-invalido-forjado"})
    assert response.status_code == 401


async def test_login_resolves_correct_tenant_even_with_two_tenants_in_the_database(client, owner_a, owner_b):
    """
    O teste que prova que o problema do 'ovo e galinha' (login sem saber
    o tenant ainda) está resolvido: dois tenants existem simultaneamente
    no banco, e cada login retorna um token para o tenant CERTO — não
    porque só existe um tenant no banco de teste (o que mascararia um
    bug de resolve_login que sempre pegasse "o primeiro" registro).
    """
    token_a_resp = await client.post("/api/v1/auth/login", json={"email": owner_a["email"], "password": owner_a["password"]})
    token_b_resp = await client.post("/api/v1/auth/login", json={"email": owner_b["email"], "password": owner_b["password"]})
    assert token_a_resp.status_code == token_b_resp.status_code == 200

    # Prova indireta: cada token só enxerga o paciente do PRÓPRIO tenant.
    headers_a = {"Authorization": f"Bearer {token_a_resp.json()['access_token']}"}
    headers_b = {"Authorization": f"Bearer {token_b_resp.json()['access_token']}"}

    create_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente exclusivo de A"}, headers=headers_a)
    assert create_resp.status_code == 201

    list_b = await client.get("/api/v1/patients", headers=headers_b)
    assert list_b.json()["items"] == []


# --- Achado F-04 (Auditoria Go-Live): mesmo e-mail em mais de um tenant ---
# Cenário real e legítimo do schema (consultor que atende duas clínicas
# com o mesmo e-mail de login) — antes da correção, resolve_login
# escolhia um tenant ARBITRÁRIO via `LIMIT 1` sem `ORDER BY`.


async def test_login_with_email_shared_across_tenants_requires_selection(client, admin_engine, tenant_a, tenant_b):
    from tests.conftest import _insert_user

    shared_email = "consultora@multi-clinica.com"
    await _insert_user(admin_engine, tenant_id=tenant_a, email=shared_email, role="financeiro", password="senha-mesma-123")
    await _insert_user(admin_engine, tenant_id=tenant_b, email=shared_email, role="financeiro", password="senha-mesma-123")

    response = await client.post("/api/v1/auth/login", json={"email": shared_email, "password": "senha-mesma-123"})

    assert response.status_code == 200
    body = response.json()
    assert body["requires_tenant_selection"] is True
    assert body.get("access_token") is None
    assert {opt["trade_name"] for opt in body["tenant_options"]} == {"Clínica A", "Clínica B"}


async def test_login_with_tenant_id_after_selection_issues_correct_token(client, admin_engine, tenant_a, tenant_b):
    from tests.conftest import _insert_user

    shared_email = "consultora2@multi-clinica.com"
    user_a = await _insert_user(admin_engine, tenant_id=tenant_a, email=shared_email, role="financeiro", password="senha-mesma-123")

    await _insert_user(admin_engine, tenant_id=tenant_b, email=shared_email, role="financeiro", password="senha-mesma-123")

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": shared_email, "password": "senha-mesma-123", "tenant_id": tenant_a},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["requires_tenant_selection"] is False
    assert "access_token" in body

    # Prova indireta de que o token é do tenant A, não de um arbitrário:
    # só enxerga pacientes de A.
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    create_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente de A via consultora"}, headers=headers)
    assert create_resp.status_code == 201
    assert user_a["tenant_id"] == tenant_a


async def test_login_with_different_password_per_tenant_is_unambiguous(client, admin_engine, tenant_a, tenant_b):
    """Quando as senhas são diferentes entre os tenants, só uma bate —
    login funciona direto, sem exigir seleção (a ambiguidade só existe
    quando a MESMA senha é válida em mais de um tenant)."""
    from tests.conftest import _insert_user

    shared_email = "consultora3@multi-clinica.com"
    await _insert_user(admin_engine, tenant_id=tenant_a, email=shared_email, role="financeiro", password="senha-de-a-123")
    await _insert_user(admin_engine, tenant_id=tenant_b, email=shared_email, role="financeiro", password="senha-de-b-456")

    response = await client.post("/api/v1/auth/login", json={"email": shared_email, "password": "senha-de-a-123"})

    assert response.status_code == 200
    body = response.json()
    assert body["requires_tenant_selection"] is False
    assert "access_token" in body


async def test_login_with_tenant_id_not_matching_any_candidate_is_rejected(client, admin_engine, tenant_a, tenant_b):
    """tenant_id forjado (não corresponde a nenhum candidato com senha
    válida) não deve vazar informação — mesmo erro genérico de sempre,
    não um 404 que confirmaria/negaria a existência do tenant."""
    from tests.conftest import _insert_user

    shared_email = "consultora4@multi-clinica.com"
    await _insert_user(admin_engine, tenant_id=tenant_a, email=shared_email, role="financeiro", password="senha-mesma-123")
    await _insert_user(admin_engine, tenant_id=tenant_b, email=shared_email, role="financeiro", password="senha-mesma-123")

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": shared_email, "password": "senha-mesma-123", "tenant_id": str(uuid_module.uuid4())},
    )

    assert response.status_code == 401
    assert "inválidos" in response.json()["detail"]
