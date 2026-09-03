"""
tests/integration/test_analytics.py

Ponta a ponta via HTTP para os Dashboards de Decisão: prova que
AnalyticsService de fato agrega o que existe no banco (não só que o
motor de insights isolado funciona — isso já é coberto por
tests/test_smart_insights_engine.py, sem banco).
"""
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Unimed Nacional", normalized_key="unimed_nacional") -> str:
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


async def _create_contract(admin_engine, tenant_id, plan_id, procedure_code, agreed_value):
    """Cria o cabeçalho (já HOMOLOGADO) + um item de preço — ver DECISÃO
    em app/sql/007_contract_intelligence.sql."""
    contract_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.contracts (id, tenant_id, insurance_plan_id, valid_from, status) "
                "VALUES (:id, :t, :plan, '2026-01-01', 'homologado')"
            ),
            {"id": contract_id, "t": tenant_id, "plan": plan_id},
        )
        await conn.execute(
            text(
                "INSERT INTO core.contract_items (tenant_id, contract_id, tuss_code, agreed_price) "
                "VALUES (:t, :contract, :code, :value)"
            ),
            {"t": tenant_id, "contract": contract_id, "code": procedure_code, "value": agreed_value},
        )
    return contract_id


def _window() -> tuple[str, str]:
    """Janela [hoje, hoje+2] — cobre agendamentos "amanhã", que é como os
    outros testes de integração (test_billing_denial_engine.py) sempre
    marcam scheduled_at, para não colidir com a regra de no-show risk
    exigir agendamento futuro."""
    today = date.today()
    return today.isoformat(), (today + timedelta(days=2)).isoformat()


async def _seed_revenue_leak_billing(client, admin_engine, tenant_id, headers, *, agreed_value=200.0, charged_value=150.0):
    """Cria convênio + contrato + paciente + consulta + fatura ABAIXO do
    valor contratado (vazamento de receita — buraco financeiro), toda via
    HTTP, mesmo padrão de test_billing_denial_engine.py."""
    plan_id = await _create_insurance_plan(admin_engine, tenant_id)
    await _create_contract(admin_engine, tenant_id, plan_id, procedure_code="10101012", agreed_value=agreed_value)

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Analytics"}, headers=headers)
    patient_id = patient_resp.json()["id"]

    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
            "cid_code": "J06",
        },
        headers=headers,
    )
    appointment_id = appointment_resp.json()["id"]

    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": charged_value},
        headers=headers,
    )
    assert billing_resp.status_code == 201
    return billing_resp.json()


async def test_executive_summary_computes_financial_hole_and_margin(client, auth_headers_a, admin_engine, tenant_a):
    await _seed_revenue_leak_billing(client, admin_engine, tenant_a, auth_headers_a, agreed_value=200.0, charged_value=150.0)
    date_from, date_to = _window()

    response = await client.get(
        f"/api/v1/analytics/executive-summary?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    body = response.json()

    assert body["total_billed"]["value"] == 150.0
    assert body["financial_hole"]["value"] == 50.0  # 200 (contratado) - 150 (cobrado)
    assert body["margin_vs_contracted_pct"] == 75.0  # 150 / (150 + 50) * 100


async def test_agenda_metrics_returns_peak_hours_and_professionals(client, auth_headers_a, admin_engine, tenant_a):
    professional_resp = await client.post(
        "/api/v1/professionals",
        json={"full_name": "Dr. Agenda", "availability": [{"weekday": 1, "start_time": "08:00:00", "end_time": "12:00:00"}]},
        headers=auth_headers_a,
    )
    assert professional_resp.status_code == 201
    date_from, date_to = _window()

    response = await client.get(
        f"/api/v1/analytics/agenda-metrics?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    body = response.json()
    assert any(p["full_name"] == "Dr. Agenda" for p in body["professionals"])
    assert "estimated_revenue_at_risk" in body


async def test_smart_insights_flags_financial_hole_from_current_period(client, auth_headers_a, admin_engine, tenant_a):
    await _seed_revenue_leak_billing(client, admin_engine, tenant_a, auth_headers_a, agreed_value=300.0, charged_value=250.0)
    date_from, date_to = _window()

    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    insights = response.json()["insights"]
    assert any(i["title"] == "Buraco financeiro identificado" and i["financial_impact"] == 50.0 for i in insights)


async def test_atendimento_cannot_access_analytics(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao3@clinica-a.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    for path in (
        "executive-summary",
        "agenda-metrics",
        "smart-insights",
        "plan-loss-ranking",
        "contract-utilization",
    ):
        response = await client.get(f"/api/v1/analytics/{path}", headers=headers)
        assert response.status_code == 403, f"{path} deveria barrar atendimento"


async def test_financeiro_can_view_analytics(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="financeiro2@clinica-a.com", role="financeiro")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.get("/api/v1/analytics/executive-summary", headers=headers)
    assert response.status_code == 200


async def test_tenant_b_never_sees_tenant_a_financial_hole(client, auth_headers_a, auth_headers_b, admin_engine, tenant_a):
    await _seed_revenue_leak_billing(client, admin_engine, tenant_a, auth_headers_a, agreed_value=200.0, charged_value=100.0)
    date_from, date_to = _window()

    response_b = await client.get(
        f"/api/v1/analytics/executive-summary?date_from={date_from}&date_to={date_to}", headers=auth_headers_b
    )
    assert response_b.status_code == 200
    assert response_b.json()["financial_hole"]["value"] == 0.0


async def test_invalid_period_returns_400(client, auth_headers_a):
    response = await client.get(
        "/api/v1/analytics/executive-summary?date_from=2026-02-01&date_to=2026-01-01", headers=auth_headers_a
    )
    assert response.status_code == 400


# --- Achado do briefing de redesenho (Auditoria Go-Live): terceiro
# exemplo, insight de meta de faturamento anual vs. ritmo real. Ver
# smart_insights_engine.py::_annual_goal_insight (algoritmo já coberto
# exaustivamente em tests/test_smart_insights_engine.py, sem banco) —
# aqui só provamos a FIAÇÃO ponta a ponta: tenant.annual_revenue_goal
# (PATCH /tenant) -> AnalyticsService -> insight na resposta HTTP real.


async def test_smart_insights_has_no_annual_goal_insight_when_goal_not_configured(client, auth_headers_a, tenant_a):
    """Decisão explícita do usuário: sem meta manual configurada em Minha
    Clínica, o insight nunca aparece — o sistema não inventa uma meta."""
    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    titles = [i["title"] for i in response.json()["insights"]]
    assert "Faturamento anual abaixo do ritmo da meta" not in titles


async def test_smart_insights_flags_annual_goal_behind_pace_when_configured(client, auth_headers_a, tenant_a):
    patch_resp = await client.patch("/api/v1/tenant", json={"annual_revenue_goal": 1_000_000.0}, headers=auth_headers_a)
    assert patch_resp.status_code == 200
    assert patch_resp.json()["annual_revenue_goal"] == 1_000_000.0

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    insights = response.json()["insights"]
    annual_insight = next((i for i in insights if i["title"] == "Faturamento anual abaixo do ritmo da meta"), None)
    # Sem nenhum faturamento no ano (banco limpo por teste), o ritmo real
    # é 0% de qualquer ritmo esperado > 0 -> sempre crítico, qualquer que
    # seja a data em que a suíte rodar.
    assert annual_insight is not None
    assert annual_insight["severity"] == "critical"
    assert "CRM" in annual_insight["message"]


# --- Painel → Faturamento: ranking de perda por convênio e utilização
# de contrato (achado da revisão de backend pedida pelo usuário: "quais
# convênios têm maior perda" e "quais contratos estão parados").


async def test_plan_loss_ranking_groups_financial_hole_by_plan(client, auth_headers_a, admin_engine, tenant_a):
    await _seed_revenue_leak_billing(
        client, admin_engine, tenant_a, auth_headers_a, agreed_value=200.0, charged_value=150.0
    )
    date_from, date_to = _window()

    response = await client.get(
        f"/api/v1/analytics/plan-loss-ranking?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    plans = response.json()["plans"]
    assert len(plans) == 1
    assert plans[0]["plan_name"] == "Unimed Nacional"
    assert plans[0]["financial_hole"] == 50.0
    # Cobrar abaixo do contratado também aciona a regra
    # "value_below_contract_revenue_leak" do motor de glosa (severidade
    # "medium" — ver denial_risk_engine.py), então o charged_value inteiro
    # (150) também entra em denial_risk_value: 50 (buraco) + 0 (gap) +
    # 150 (charged_value sob risco) = 200.
    assert plans[0]["denial_risk_value"] == 150.0
    assert plans[0]["total_loss"] == 200.0


async def test_plan_loss_ranking_orders_by_total_loss_descending(client, auth_headers_a, admin_engine, tenant_a):
    await _seed_revenue_leak_billing(
        client, admin_engine, tenant_a, auth_headers_a, agreed_value=150.0, charged_value=140.0
    )  # convênio A: buraco de 10
    plan_b = await _create_insurance_plan(admin_engine, tenant_a, display_name="Bradesco Saúde", normalized_key="bradesco_saude")
    await _create_contract(admin_engine, tenant_a, plan_b, procedure_code="20202020", agreed_value=500.0)
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente B"}, headers=auth_headers_a)
    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_resp.json()["id"],
            "insurance_plan_id": plan_b,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "20202020",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )
    await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_resp.json()["id"], "insurance_plan_id": plan_b, "charged_value": 100.0},
        headers=auth_headers_a,
    )  # convênio B: buraco de 400 — deve vir primeiro no ranking
    date_from, date_to = _window()

    response = await client.get(
        f"/api/v1/analytics/plan-loss-ranking?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    plans = response.json()["plans"]
    assert [p["plan_name"] for p in plans] == ["Bradesco Saúde", "Unimed Nacional"]


async def test_contract_utilization_flags_unbilled_items(client, auth_headers_a, admin_engine, tenant_a):
    """Contrato com 2 procedimentos negociados, só 1 faturado no
    período -> 50% de utilização e idle_catalog_value = preço do item
    parado."""
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    contract_id = await _create_contract(admin_engine, tenant_a, plan_id, procedure_code="10101012", agreed_value=200.0)
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.contract_items (tenant_id, contract_id, tuss_code, agreed_price) "
                "VALUES (:t, :c, '30303030', 80.0)"
            ),
            {"t": tenant_a, "c": contract_id},
        )

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Utilização"}, headers=auth_headers_a)
    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_resp.json()["id"],
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )
    await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_resp.json()["id"], "insurance_plan_id": plan_id, "charged_value": 200.0},
        headers=auth_headers_a,
    )
    date_from, date_to = _window()

    response = await client.get(
        f"/api/v1/analytics/contract-utilization?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    contracts = response.json()["contracts"]
    assert len(contracts) == 1
    entry = contracts[0]
    assert entry["total_items"] == 2
    assert entry["items_billed"] == 1
    assert entry["utilization_pct"] == 50.0
    assert entry["idle_catalog_value"] == 80.0


async def test_agenda_metrics_includes_patient_no_show_ranking(client, auth_headers_a, admin_engine, tenant_a):
    """Paciente com 3 atendimentos no período, 2 deles faltas -> entra na
    lista vermelha (amostra mínima e pelo menos 1 falta)."""
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Faltoso"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    date_from, date_to = _window()
    scheduled_base = datetime.combine(date.fromisoformat(date_from), datetime.min.time(), tzinfo=timezone.utc)

    async with admin_engine.begin() as conn:
        for i, status in enumerate(("no_show", "no_show", "completed")):
            await conn.execute(
                text(
                    "INSERT INTO core.appointments (tenant_id, patient_id, scheduled_at, status) "
                    "VALUES (:t, :p, :dt, :status)"
                ),
                {"t": tenant_a, "p": patient_id, "dt": scheduled_base + timedelta(hours=i), "status": status},
            )

    response = await client.get(
        f"/api/v1/analytics/agenda-metrics?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    ranking = response.json()["patient_no_show_ranking"]
    assert len(ranking) == 1
    assert ranking[0]["patient_id"] == patient_id
    assert ranking[0]["no_show_count"] == 2
    assert ranking[0]["total_appointments"] == 3
    assert round(ranking[0]["no_show_rate"], 4) == round(2 / 3, 4)


async def test_agenda_metrics_excludes_patients_below_minimum_sample(client, auth_headers_a, admin_engine, tenant_a):
    """1 falta em 1 único atendimento é 100% de taxa, mas amostra
    estatisticamente vazia — não deve aparecer na lista vermelha
    (RED_LIST_MIN_SAMPLE = 3 em analytics_service.py)."""
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Amostra Baixa"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    date_from, date_to = _window()
    scheduled_at = datetime.combine(date.fromisoformat(date_from), datetime.min.time(), tzinfo=timezone.utc)

    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.appointments (tenant_id, patient_id, scheduled_at, status) VALUES (:t, :p, :dt, 'no_show')"),
            {"t": tenant_a, "p": patient_id, "dt": scheduled_at},
        )

    response = await client.get(
        f"/api/v1/analytics/agenda-metrics?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    assert response.json()["patient_no_show_ranking"] == []
