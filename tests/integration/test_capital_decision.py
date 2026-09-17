"""
tests/integration/test_capital_decision.py

GET /analytics/capital-decision-base-data — Épico F3.4 do Plano Diretor
("Decisões de capital: contratar/expandir — simulação de payback de
contratação"). O endpoint nunca devolve a decisão pronta, só o
dado-base real (receita/margem por hora observada, filtrada por
especialidade quando há amostra; faturamento médio das outras unidades
do mesmo grupo) que o frontend usa pra simular o payback.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Capital Saúde", normalized_key="capital_saude") -> str:
    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :name, :key)"),
            {"id": plan_id, "t": tenant_id, "name": display_name, "key": normalized_key},
        )
    return plan_id


async def _bill_professional(
    client,
    auth_headers,
    admin_engine,
    tenant_id,
    *,
    full_name: str,
    specialty: str | None,
    charged_value: float,
    duration_minutes: int = 60,
    plan_suffix: str,
) -> str:
    professional_resp = await client.post(
        "/api/v1/professionals",
        json={"full_name": full_name, "specialty": specialty},
        headers=auth_headers,
    )
    assert professional_resp.status_code == 201, professional_resp.text
    professional_id = professional_resp.json()["id"]

    plan_id = await _create_insurance_plan(
        admin_engine, tenant_id, display_name=f"Capital {plan_suffix}", normalized_key=f"capital_{plan_suffix}"
    )
    patient_resp = await client.post("/api/v1/patients", json={"full_name": f"Paciente {full_name}"}, headers=auth_headers)
    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_resp.json()["id"],
            "insurance_plan_id": plan_id,
            "professional_id": professional_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "duration_minutes": duration_minutes,
        },
        headers=auth_headers,
    )
    assert appointment_resp.status_code == 201, appointment_resp.text
    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_resp.json()["id"], "insurance_plan_id": plan_id, "charged_value": charged_value},
        headers=auth_headers,
    )
    assert billing_resp.status_code == 201, billing_resp.text
    return professional_id


async def test_capital_decision_base_data_is_honestly_empty_for_a_fresh_tenant(client, auth_headers_a):
    response = await client.get("/api/v1/analytics/capital-decision-base-data", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["sample_size"] == 0
    assert body["avg_revenue_per_hour"] is None
    assert body["has_cost_data"] is False
    assert body["avg_margin_per_hour"] is None
    assert body["used_fallback_clinic_wide"] is False
    assert body["available_specialties"] == []
    assert body["belongs_to_organization"] is False
    assert body["sibling_units_count"] == 0
    assert body["avg_monthly_revenue_per_unit"] is None


async def test_capital_decision_base_data_averages_revenue_per_hour_clinic_wide(client, auth_headers_a, admin_engine, tenant_a):
    # R$300/h e R$100/h -> média simples de R$200/h (sample_size=2 >= min_sample).
    await _bill_professional(
        client, auth_headers_a, admin_engine, tenant_a,
        full_name="Dr. Caro", specialty=None, charged_value=300.0, duration_minutes=60, plan_suffix="caro",
    )
    await _bill_professional(
        client, auth_headers_a, admin_engine, tenant_a,
        full_name="Dr. Barato", specialty=None, charged_value=100.0, duration_minutes=60, plan_suffix="barato",
    )

    response = await client.get("/api/v1/analytics/capital-decision-base-data", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["sample_size"] == 2
    assert body["avg_revenue_per_hour"] == 200.0
    assert body["used_fallback_clinic_wide"] is False


async def test_capital_decision_base_data_filters_by_specialty_with_enough_sample(client, auth_headers_a, admin_engine, tenant_a):
    await _bill_professional(
        client, auth_headers_a, admin_engine, tenant_a,
        full_name="Dr. Cardio 1", specialty="Cardiologia", charged_value=300.0, duration_minutes=60, plan_suffix="cardio1",
    )
    await _bill_professional(
        client, auth_headers_a, admin_engine, tenant_a,
        full_name="Dr. Cardio 2", specialty="Cardiologia", charged_value=100.0, duration_minutes=60, plan_suffix="cardio2",
    )
    await _bill_professional(
        client, auth_headers_a, admin_engine, tenant_a,
        full_name="Dr. Orto", specialty="Ortopedia", charged_value=1000.0, duration_minutes=60, plan_suffix="orto",
    )

    # Busca em minúsculo de propósito -> confirma o match case-insensitive.
    response = await client.get(
        "/api/v1/analytics/capital-decision-base-data?specialty=cardiologia", headers=auth_headers_a
    )
    assert response.status_code == 200
    body = response.json()
    assert body["specialty_requested"] == "cardiologia"
    assert body["used_fallback_clinic_wide"] is False
    assert body["sample_size"] == 2
    assert body["avg_revenue_per_hour"] == 200.0  # só os 2 cardiologistas, não o ortopedista de R$1000/h
    assert set(body["available_specialties"]) == {"Cardiologia", "Ortopedia"}


async def test_capital_decision_base_data_falls_back_to_clinic_wide_without_enough_specialty_sample(
    client, auth_headers_a, admin_engine, tenant_a
):
    # Só 1 profissional de Dermatologia (< min_sample=2) -> cai pra
    # média de TODA a clínica em vez de devolver None.
    await _bill_professional(
        client, auth_headers_a, admin_engine, tenant_a,
        full_name="Dr. Derma", specialty="Dermatologia", charged_value=100.0, duration_minutes=60, plan_suffix="derma",
    )
    await _bill_professional(
        client, auth_headers_a, admin_engine, tenant_a,
        full_name="Dr. Outro", specialty="Outra", charged_value=300.0, duration_minutes=60, plan_suffix="outra",
    )

    response = await client.get(
        "/api/v1/analytics/capital-decision-base-data?specialty=Dermatologia", headers=auth_headers_a
    )
    assert response.status_code == 200
    body = response.json()
    assert body["used_fallback_clinic_wide"] is True
    assert body["sample_size"] == 2  # caiu pro pool inteiro da clínica
    assert body["avg_revenue_per_hour"] == 200.0


async def test_capital_decision_base_data_includes_margin_per_hour_when_cost_data_exists(
    client, auth_headers_a, admin_engine, tenant_a
):
    # 2 profissionais (>= min_sample) pra ter uma MÉDIA de verdade — não
    # só o caso extremo de 1 profissional só.
    professional_id = await _bill_professional(
        client, auth_headers_a, admin_engine, tenant_a,
        full_name="Dr. Margem Capital", specialty=None, charged_value=1000.0, duration_minutes=60, plan_suffix="margemcap",
    )
    await _bill_professional(
        client, auth_headers_a, admin_engine, tenant_a,
        full_name="Dr. Sem Custo Direto", specialty=None, charged_value=600.0, duration_minutes=60, plan_suffix="margemcap2",
    )
    from datetime import date

    period_month = date.today().replace(day=1).isoformat()
    # Custo DIRETO só do primeiro profissional; sem custo GERAL nenhum
    # -> o segundo profissional não tem custo nenhum alocado (rateio
    # geral é 0), mas ainda entra no `has_cost_data=True` da clínica.
    cost_resp = await client.post(
        "/api/v1/cost-entries",
        json={"category": "comissao_repasse", "amount": 400.0, "period_month": period_month, "professional_id": professional_id},
        headers=auth_headers_a,
    )
    assert cost_resp.status_code == 201

    response = await client.get("/api/v1/analytics/capital-decision-base-data", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["has_cost_data"] is True
    # Profissional 1: R$1000/h - R$400/h de custo direto = R$600/h de margem.
    # Profissional 2: R$600/h - R$0 (sem custo direto nem rateio geral) = R$600/h.
    # Média: R$600/h.
    assert body["avg_margin_per_hour"] == 600.0


async def test_capital_decision_base_data_sibling_units_average_excludes_requesting_tenant(
    client, auth_headers_a, admin_engine, tenant_a
):
    org_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(text("INSERT INTO core.organizations (id, name) VALUES (:id, :name)"), {"id": org_id, "name": "Grupo Capital"})
        await conn.execute(text("UPDATE core.tenants SET organization_id = :org WHERE id = :t"), {"org": org_id, "t": tenant_a})

    sibling_a_id = str(uuid.uuid4())
    sibling_b_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        for sibling_id, name in ((sibling_a_id, "Unidade Norte"), (sibling_b_id, "Unidade Sul")):
            await conn.execute(
                text(
                    "INSERT INTO core.tenants (id, legal_name, trade_name, cnpj, is_active, organization_id) "
                    "VALUES (:id, :n, :n, :cnpj, true, :org)"
                ),
                {"id": sibling_id, "n": name, "cnpj": uuid.uuid4().hex[:14], "org": org_id},
            )

    async def _seed_unit_billing(tenant_id: str, charged_value: float) -> None:
        patient_id = str(uuid.uuid4())
        plan_id = str(uuid.uuid4())
        appointment_id = str(uuid.uuid4())
        async with admin_engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO core.patients (id, tenant_id, full_name) VALUES (:id, :t, 'Paciente Unidade Capital')"),
                {"id": patient_id, "t": tenant_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) "
                    "VALUES (:id, :t, 'Convênio Unidade Capital', :key)"
                ),
                {"id": plan_id, "t": tenant_id, "key": f"convenio_capital_{plan_id[:8]}"},
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
                    "INSERT INTO core.billing (tenant_id, appointment_id, insurance_plan_id, charged_value) "
                    "VALUES (:t, :a, :plan, :value)"
                ),
                {"t": tenant_id, "a": appointment_id, "plan": plan_id, "value": charged_value},
            )

    await _seed_unit_billing(sibling_a_id, 1000.0)
    await _seed_unit_billing(sibling_b_id, 3000.0)

    response = await client.get("/api/v1/analytics/capital-decision-base-data", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["belongs_to_organization"] is True
    assert body["sibling_units_count"] == 2
    # (1000 + 3000) / 2 = 2000 -- a própria unidade (tenant_a, sem
    # faturamento aqui) nunca entra nessa média.
    assert body["avg_monthly_revenue_per_unit"] == 2000.0


async def test_atendimento_cannot_access_capital_decision_base_data(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao@capital-decision.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.get("/api/v1/analytics/capital-decision-base-data", headers=headers)
    assert response.status_code == 403
