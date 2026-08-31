"""
tests/integration/test_denial_appeals.py

Ponta a ponta via HTTP: cria paciente -> convênio -> consulta -> fatura,
abre um Recurso de Glosa sobre essa fatura, percorre a máquina de estados
(aberto -> protocolado -> deferido/indeferido/NIP) e confere o cálculo de
prazo (app/services/appeal_deadline_calculator.py) através da pilha real.
"""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Amil One", normalized_key="amil_one") -> str:
    import uuid

    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) "
                "VALUES (:id, :t, :name, :key)"
            ),
            {"id": plan_id, "t": tenant_id, "name": display_name, "key": normalized_key},
        )
    return plan_id


async def _create_billing(client, auth_headers, plan_id: str) -> str:
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Recurso"}, headers=auth_headers)
    patient_id = patient_resp.json()["id"]

    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
            "cid_code": "Z00.0",
        },
        headers=auth_headers,
    )
    assert appointment_resp.status_code == 201
    appointment_id = appointment_resp.json()["id"]

    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 150.0},
        headers=auth_headers,
    )
    assert billing_resp.status_code == 201
    return billing_resp.json()["id"]


async def test_create_appeal_uses_default_deadline_when_company_has_none(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, auth_headers_a, plan_id)

    denied_at = date.today().isoformat()
    response = await client.post(
        "/api/v1/denial-appeals",
        json={
            "billing_id": billing_id,
            "appeal_type": "administrativa",
            "operator_denial_reason": "Falta de guia de autorização prévia.",
            "denied_at": denied_at,
        },
        headers=auth_headers_a,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "aberto"
    # Sem operadora/plano configurado com default_appeal_deadline_days,
    # cai no fallback settings.DEFAULT_APPEAL_DEADLINE_DAYS (30) — ver
    # DECISÃO em app/core/config.py.
    expected_deadline = (date.today() + timedelta(days=30)).isoformat()
    assert body["deadline_at"] == expected_deadline


async def test_appeal_lifecycle_aberto_to_protocolado_to_deferido(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, auth_headers_a, plan_id)

    create_resp = await client.post(
        "/api/v1/denial-appeals",
        json={
            "billing_id": billing_id,
            "appeal_type": "medica",
            "denied_at": date.today().isoformat(),
        },
        headers=auth_headers_a,
    )
    appeal_id = create_resp.json()["id"]

    # Não pode resolver direto de 'aberto' — precisa protocolar primeiro.
    premature_resolve = await client.post(
        f"/api/v1/denial-appeals/{appeal_id}/resolve",
        json={"status": "deferido"},
        headers=auth_headers_a,
    )
    assert premature_resolve.status_code == 409

    file_resp = await client.post(f"/api/v1/denial-appeals/{appeal_id}/file", json={}, headers=auth_headers_a)
    assert file_resp.status_code == 200
    assert file_resp.json()["status"] == "protocolado"
    assert file_resp.json()["filed_at"] is not None

    resolve_resp = await client.post(
        f"/api/v1/denial-appeals/{appeal_id}/resolve",
        json={"status": "deferido", "resolution_notes": "Operadora reconsiderou após envio de laudo."},
        headers=auth_headers_a,
    )
    assert resolve_resp.status_code == 200
    body = resolve_resp.json()
    assert body["status"] == "deferido"
    assert body["resolved_at"] is not None


async def test_indeferido_can_be_escalated_to_nip(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, auth_headers_a, plan_id)

    create_resp = await client.post(
        "/api/v1/denial-appeals",
        json={"billing_id": billing_id, "appeal_type": "administrativa", "denied_at": date.today().isoformat()},
        headers=auth_headers_a,
    )
    appeal_id = create_resp.json()["id"]
    await client.post(f"/api/v1/denial-appeals/{appeal_id}/file", json={}, headers=auth_headers_a)

    indeferido_resp = await client.post(
        f"/api/v1/denial-appeals/{appeal_id}/resolve", json={"status": "indeferido"}, headers=auth_headers_a
    )
    assert indeferido_resp.status_code == 200
    # 'indeferido' NÃO é terminal por si só — o caso pode ser escalado.
    assert indeferido_resp.json()["resolved_at"] is not None

    nip_resp = await client.post(
        f"/api/v1/denial-appeals/{appeal_id}/resolve", json={"status": "nip_aberta"}, headers=auth_headers_a
    )
    assert nip_resp.status_code == 200
    # nip_aberta é uma ESCALADA, não uma resolução final — não deveria
    # sobrescrever resolved_at com um novo timestamp (o caso continua
    # aberto, agora na ANS).
    assert nip_resp.json()["status"] == "nip_aberta"


async def test_atendimento_cannot_create_denial_appeal(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao@denial-appeals-test.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.post(
        "/api/v1/denial-appeals",
        json={
            "billing_id": "00000000-0000-0000-0000-000000000000",
            "appeal_type": "administrativa",
            "denied_at": date.today().isoformat(),
        },
        headers=headers,
    )
    assert response.status_code == 403
