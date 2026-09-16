"""
tests/integration/test_organization_summary.py

GET /analytics/organization-summary — Épico F3.2 do Plano Diretor
("Consolidação multi-unidade"). Prova que o dashboard consolidado
agrupa direito as unidades da MESMA organization_id (nunca de outra),
que a clínica avulsa (organization_id NULL, estado normal) recebe uma
resposta honesta sem erro, e que os totais consolidados somam as
unidades certas.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text


async def _insert_tenant_with_org(admin_engine, *, trade_name: str, organization_id: str | None) -> str:
    tenant_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.tenants (id, legal_name, trade_name, cnpj, is_active, organization_id) "
                "VALUES (:id, :n, :n, :cnpj, true, :org)"
            ),
            {"id": tenant_id, "n": trade_name, "cnpj": uuid.uuid4().hex[:14], "org": organization_id},
        )
    return tenant_id


async def _insert_organization(admin_engine, *, name: str) -> str:
    org_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(text("INSERT INTO core.organizations (id, name) VALUES (:id, :name)"), {"id": org_id, "name": name})
    return org_id


async def _seed_billing(admin_engine, tenant_id: str, *, charged_value: float, denial_risk_level: str = "low") -> None:
    patient_id = str(uuid.uuid4())
    plan_id = str(uuid.uuid4())
    appointment_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.patients (id, tenant_id, full_name) VALUES (:id, :t, 'Paciente Unidade')"),
            {"id": patient_id, "t": tenant_id},
        )
        await conn.execute(
            text(
                "INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) "
                "VALUES (:id, :t, 'Convênio Unidade', :key)"
            ),
            {"id": plan_id, "t": tenant_id, "key": f"convenio_unidade_{plan_id[:8]}"},
        )
        await conn.execute(
            text(
                "INSERT INTO core.appointments (id, tenant_id, patient_id, scheduled_at, status) "
                "VALUES (:id, :t, :p, now(), 'completed')"
            ),
            {"id": appointment_id, "t": tenant_id, "p": patient_id},
        )
        await conn.execute(
            text(
                "INSERT INTO core.billing (tenant_id, appointment_id, insurance_plan_id, charged_value, denial_risk_level) "
                "VALUES (:t, :a, :plan, :value, :risk)"
            ),
            {"t": tenant_id, "a": appointment_id, "plan": plan_id, "value": charged_value, "risk": denial_risk_level},
        )


async def test_standalone_tenant_does_not_belong_to_organization(client, auth_headers_a):
    """Estado NORMAL da maioria das clínicas (organization_id NULL) —
    resposta honesta, nunca um erro nem uma lista vazia disfarçada de
    'erro de configuração'."""
    response = await client.get("/api/v1/analytics/organization-summary", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["belongs_to_organization"] is False
    assert body["organization_name"] is None
    assert body["units"] == []
    assert body["consolidated_total_billed"] == 0.0
    assert body["consolidated_denial_risk_pct"] is None
    assert body["consolidated_no_show_rate"] is None


async def test_organization_summary_lists_only_units_of_same_organization(
    client, auth_headers_a, admin_engine, tenant_a
):
    org_id = await _insert_organization(admin_engine, name="Grupo Clínica Alfa")
    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE core.tenants SET organization_id = :org WHERE id = :t"), {"org": org_id, "t": tenant_a})
    sibling = await _insert_tenant_with_org(admin_engine, trade_name="Unidade Zona Sul", organization_id=org_id)
    # Clínica de OUTRA organização — nunca deveria aparecer.
    other_org_id = await _insert_organization(admin_engine, name="Grupo Concorrente")
    await _insert_tenant_with_org(admin_engine, trade_name="Clínica de Outro Grupo", organization_id=other_org_id)
    # Clínica avulsa (sem organização) — também nunca deveria aparecer.
    await _insert_tenant_with_org(admin_engine, trade_name="Clínica Avulsa Qualquer", organization_id=None)

    await _seed_billing(admin_engine, tenant_a, charged_value=1000.0, denial_risk_level="low")
    await _seed_billing(admin_engine, sibling, charged_value=500.0, denial_risk_level="high")

    response = await client.get("/api/v1/analytics/organization-summary", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()

    assert body["belongs_to_organization"] is True
    assert body["organization_name"] == "Grupo Clínica Alfa"
    assert {u["trade_name"] for u in body["units"]} == {"Clínica A", "Unidade Zona Sul"}

    requesting_unit = next(u for u in body["units"] if u["is_requesting_tenant"])
    assert requesting_unit["trade_name"] == "Clínica A"
    assert requesting_unit["total_billed"] == 1000.0
    assert requesting_unit["denial_risk_pct"] == 0.0

    sibling_unit = next(u for u in body["units"] if not u["is_requesting_tenant"])
    assert sibling_unit["trade_name"] == "Unidade Zona Sul"
    assert sibling_unit["total_billed"] == 500.0
    assert sibling_unit["denial_risk_pct"] == 1.0

    # Consolidado: 1500 faturado, 500 em risco -> 33.33...%.
    assert body["consolidated_total_billed"] == 1500.0
    assert abs(body["consolidated_denial_risk_pct"] - (500 / 1500)) < 1e-9


async def test_organization_summary_unit_without_billing_has_none_denial_rate(
    client, auth_headers_a, admin_engine, tenant_a
):
    org_id = await _insert_organization(admin_engine, name="Grupo Sem Faturamento")
    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE core.tenants SET organization_id = :org WHERE id = :t"), {"org": org_id, "t": tenant_a})
    await _insert_tenant_with_org(admin_engine, trade_name="Unidade Nova Sem Faturamento", organization_id=org_id)
    # Sem seed de billing nenhum -> base zero.

    response = await client.get("/api/v1/analytics/organization-summary", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    for unit in body["units"]:
        assert unit["total_billed"] == 0.0
        assert unit["denial_risk_pct"] is None
    assert body["consolidated_denial_risk_pct"] is None


async def test_atendimento_cannot_access_organization_summary(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao@org-summary.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.get("/api/v1/analytics/organization-summary", headers=headers)
    assert response.status_code == 403


async def test_organization_summary_ignores_billing_outside_window(client, auth_headers_a, admin_engine, tenant_a):
    org_id = await _insert_organization(admin_engine, name="Grupo Janela")
    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE core.tenants SET organization_id = :org WHERE id = :t"), {"org": org_id, "t": tenant_a})

    # Billing DENTRO da janela de 30 dias.
    await _seed_billing(admin_engine, tenant_a, charged_value=200.0)

    # Billing FORA da janela (60 dias atrás) — inserido direto com
    # created_at antigo, nunca deveria entrar no consolidado.
    patient_id = str(uuid.uuid4())
    plan_id = str(uuid.uuid4())
    appointment_id = str(uuid.uuid4())
    old_created_at = datetime.now(timezone.utc) - timedelta(days=60)
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.patients (id, tenant_id, full_name) VALUES (:id, :t, 'Paciente Antigo')"),
            {"id": patient_id, "t": tenant_a},
        )
        await conn.execute(
            text(
                "INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) "
                "VALUES (:id, :t, 'Convênio Antigo', 'convenio_antigo_janela')"
            ),
            {"id": plan_id, "t": tenant_a},
        )
        await conn.execute(
            text(
                "INSERT INTO core.appointments (id, tenant_id, patient_id, scheduled_at, status) "
                "VALUES (:id, :t, :p, now(), 'completed')"
            ),
            {"id": appointment_id, "t": tenant_a, "p": patient_id},
        )
        await conn.execute(
            text(
                "INSERT INTO core.billing (tenant_id, appointment_id, insurance_plan_id, charged_value, created_at) "
                "VALUES (:t, :a, :plan, 9999.0, :created_at)"
            ),
            {"t": tenant_a, "a": appointment_id, "plan": plan_id, "created_at": old_created_at},
        )

    response = await client.get("/api/v1/analytics/organization-summary", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    unit = next(u for u in body["units"] if u["is_requesting_tenant"])
    assert unit["total_billed"] == 200.0
