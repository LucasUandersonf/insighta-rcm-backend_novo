"""
tests/integration/test_rls_isolation.py

O TESTE MAIS IMPORTANTE DESTE PROJETO. Tudo mais (glosa, capacidade,
risco de falta) é lógica de produto; isto aqui é a garantia de segurança
que sustenta o SaaS inteiro — a Clínica A nunca pode ver ou alterar dado
da Clínica B, mesmo que tente diretamente pelo ID.

Só tem "dente" porque o app engine conecta como `app_test_runtime`
(NOSUPERUSER, NOBYPASSRLS) — ver DECISÃO CRÍTICA #2 em conftest.py. Se
alguém reverter isso para conectar como superusuário, estes testes
"passam" mesmo com RLS quebrado — então qualquer alteração em
conftest.py que mexa na role de conexão deve ser tratada como mudança de
alto risco.
"""
from datetime import date, datetime, timedelta, timezone

import pytest


async def _create_patient(client, headers, full_name="Paciente Teste") -> str:
    response = await client.post("/api/v1/patients", json={"full_name": full_name}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _create_appointment(client, headers, patient_id, scheduled_at=None) -> str:
    scheduled_at = scheduled_at or (datetime.now(timezone.utc) + timedelta(days=1))
    response = await client.post(
        "/api/v1/appointments",
        json={"patient_id": patient_id, "scheduled_at": scheduled_at.isoformat()},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_tenant_b_cannot_list_tenant_a_patients(client, auth_headers_a, auth_headers_b):
    await _create_patient(client, auth_headers_a, "Paciente da Clínica A")

    response_b = await client.get("/api/v1/patients", headers=auth_headers_b)
    assert response_b.status_code == 200
    assert response_b.json()["items"] == [], "Clínica B não deveria ver nenhum paciente da Clínica A"

    response_a = await client.get("/api/v1/patients", headers=auth_headers_a)
    assert response_a.json()["total"] == 1
    assert len(response_a.json()["items"]) == 1


async def test_tenant_b_gets_404_fetching_tenant_a_appointment_by_id(client, auth_headers_a, auth_headers_b):
    patient_id = await _create_patient(client, auth_headers_a)
    appointment_id = await _create_appointment(client, auth_headers_a, patient_id)

    # Tenant B tenta acessar o MESMO patient_id (criando um appointment
    # próprio referenciando o paciente de A) — deve ser bloqueado com 404,
    # não com um erro de permissão que revelaria que o ID existe em algum
    # lugar (ver princípio de "não revelar existência" já usado no login).
    response = await client.post(
        "/api/v1/appointments",
        json={"patient_id": patient_id, "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
        headers=auth_headers_b,
    )
    assert response.status_code == 404

    # E o inverso: B nunca vê o appointment que A criou, nem por listagem.
    list_response = await client.get(f"/api/v1/appointments/by-patient/{patient_id}", headers=auth_headers_b)
    assert list_response.status_code == 200
    assert list_response.json() == []


async def test_tenant_b_cannot_create_billing_against_tenant_a_appointment(client, auth_headers_a, auth_headers_b):
    patient_id = await _create_patient(client, auth_headers_a)
    appointment_id = await _create_appointment(client, auth_headers_a, patient_id)

    # Financeiro da Clínica B tenta faturar em cima de um appointment que,
    # do ponto de vista dele, simplesmente não existe.
    response = await client.post(
        "/api/v1/billing",
        json={
            "appointment_id": appointment_id,
            "insurance_plan_id": "00000000-0000-0000-0000-000000000000",
            "charged_value": 100.0,
        },
        headers=auth_headers_b,
    )
    assert response.status_code == 404


async def test_professionals_are_isolated_per_tenant(client, auth_headers_a, auth_headers_b):
    create_response = await client.post(
        "/api/v1/professionals", json={"full_name": "Dr. Fulano"}, headers=auth_headers_a
    )
    assert create_response.status_code == 201

    list_b = await client.get("/api/v1/professionals", headers=auth_headers_b)
    assert list_b.json() == [], "Clínica B não deveria ver profissionais da Clínica A"


async def test_direct_row_access_across_tenants_is_blocked_at_the_database_level(admin_engine, tenant_a, tenant_b, app_runtime_dsn):
    """
    Vai um nível abaixo da API: conecta como app_test_runtime (a MESMA
    role que a aplicação usa), seta o contexto de tenant B via SET LOCAL,
    e tenta SELECT numa linha que pertence ao tenant A — o Postgres, não
    o código Python, precisa devolver zero linhas.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    runtime_engine = create_async_engine(app_runtime_dsn)

    try:
        # Insere um paciente do tenant A usando o superusuário (setup),
        # depois tenta lê-lo como app_test_runtime "logado" como tenant B.
        async with admin_engine.begin() as conn:
            result = await conn.execute(
                text(
                    "INSERT INTO core.patients (tenant_id, full_name) VALUES (:t, 'Paciente A') RETURNING id"
                ),
                {"t": tenant_a},
            )
            patient_id = result.scalar_one()

        async with runtime_engine.connect() as conn:
            await conn.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": tenant_b})
            result = await conn.execute(text("SELECT * FROM core.patients WHERE id = :id"), {"id": patient_id})
            rows = result.fetchall()

        assert rows == [], "RLS falhou: uma sessão logada como tenant B conseguiu ler linha do tenant A"
    finally:
        await runtime_engine.dispose()
