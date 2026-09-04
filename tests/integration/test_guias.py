"""
tests/integration/test_guias.py

Fase 1 do plano de adequação ao fluxo real de mercado (Agendamento ->
Atendimento -> Faturamento — ver conversa/MODERNANET_REFERENCIA.md):
Guia (TISS) como entidade própria, com Billing.guia_id ligando N
lançamentos a 1 guia (ex.: SADT com vários procedimentos).
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Unimed Nacional", normalized_key="unimed_nacional") -> str:
    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :n, :k)"),
            {"id": plan_id, "t": tenant_id, "n": display_name, "k": normalized_key},
        )
    return plan_id


async def test_create_and_get_guia(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)

    create_resp = await client.post(
        "/api/v1/guias",
        json={
            "insurance_plan_id": plan_id,
            "tipo": "sadt",
            "numero": "12345678",
            "senha": "987654",
            "senha_validade": "2026-12-31",
            "tabela_procedimento": "22",
        },
        headers=auth_headers_a,
    )
    assert create_resp.status_code == 201, create_resp.text
    guia = create_resp.json()
    assert guia["tipo"] == "sadt"
    assert guia["numero"] == "12345678"
    assert guia["senha"] == "987654"
    assert guia["tabela_procedimento"] == "22"

    get_resp = await client.get(f"/api/v1/guias/{guia['id']}", headers=auth_headers_a)
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == guia["id"]


async def test_create_guia_rejects_unknown_tipo(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    resp = await client.post(
        "/api/v1/guias",
        json={"insurance_plan_id": plan_id, "tipo": "internacao_completa"},
        headers=auth_headers_a,
    )
    assert resp.status_code == 422


async def test_list_guias_paginated(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    for tipo in ("consulta", "sadt", "honorario"):
        resp = await client.post(
            "/api/v1/guias", json={"insurance_plan_id": plan_id, "tipo": tipo}, headers=auth_headers_a
        )
        assert resp.status_code == 201

    list_resp = await client.get("/api/v1/guias?limit=2&offset=0", headers=auth_headers_a)
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2


async def test_patch_guia_fills_in_authorization_password_later(client, auth_headers_a, admin_engine, tenant_a):
    """Caso real: a guia é criada antes da autorização (senha) existir —
    só depois que a operadora autoriza é que senha/validade ficam
    conhecidas."""
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    create_resp = await client.post(
        "/api/v1/guias", json={"insurance_plan_id": plan_id, "tipo": "consulta"}, headers=auth_headers_a
    )
    guia_id = create_resp.json()["id"]
    assert create_resp.json()["senha"] is None

    patch_resp = await client.patch(
        f"/api/v1/guias/{guia_id}",
        json={"senha": "111222", "senha_validade": "2026-10-01"},
        headers=auth_headers_a,
    )
    assert patch_resp.status_code == 200
    updated = patch_resp.json()
    assert updated["senha"] == "111222"
    assert updated["senha_validade"] == "2026-10-01"
    assert updated["tipo"] == "consulta"  # não mudou


async def test_get_nonexistent_guia_returns_404(client, auth_headers_a):
    resp = await client.get("/api/v1/guias/00000000-0000-0000-0000-000000000000", headers=auth_headers_a)
    assert resp.status_code == 404


async def test_billing_can_reference_a_guia(client, auth_headers_a, admin_engine, tenant_a):
    """Prova a ligação Billing.guia_id -> Guia — o caso real de uma guia
    SADT com múltiplos itens de billing seria N chamadas de POST /billing
    com o MESMO guia_id."""
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)

    guia_resp = await client.post(
        "/api/v1/guias", json={"insurance_plan_id": plan_id, "tipo": "sadt"}, headers=auth_headers_a
    )
    guia_id = guia_resp.json()["id"]

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Guia"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "40801016",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]

    billing_resp = await client.post(
        "/api/v1/billing",
        json={
            "appointment_id": appointment_id,
            "insurance_plan_id": plan_id,
            "charged_value": 35.0,
            "guia_id": guia_id,
        },
        headers=auth_headers_a,
    )
    assert billing_resp.status_code == 201, billing_resp.text
    assert billing_resp.json()["guia_id"] == guia_id


async def test_billing_rejects_guia_id_from_other_tenant(client, auth_headers_b, auth_headers_a, admin_engine, tenant_a, tenant_b):
    """RLS: uma guia da Clínica A não pode ser referenciada por um
    billing da Clínica B — get_by_id() já a esconde (RLS), então o
    resultado é o mesmo 404 de "não existe"."""
    plan_id_a = await _create_insurance_plan(admin_engine, tenant_a)
    guia_resp = await client.post(
        "/api/v1/guias", json={"insurance_plan_id": plan_id_a, "tipo": "consulta"}, headers=auth_headers_a
    )
    guia_id_from_a = guia_resp.json()["id"]

    plan_id_b = await _create_insurance_plan(admin_engine, tenant_b, display_name="SulAmérica", normalized_key="sulamerica")
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente B"}, headers=auth_headers_b)
    patient_id_b = patient_resp.json()["id"]
    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id_b,
            "insurance_plan_id": plan_id_b,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
            "cid_code": "J06",
        },
        headers=auth_headers_b,
    )
    appointment_id_b = appointment_resp.json()["id"]

    billing_resp = await client.post(
        "/api/v1/billing",
        json={
            "appointment_id": appointment_id_b,
            "insurance_plan_id": plan_id_b,
            "charged_value": 100.0,
            "guia_id": guia_id_from_a,
        },
        headers=auth_headers_b,
    )
    assert billing_resp.status_code == 404
