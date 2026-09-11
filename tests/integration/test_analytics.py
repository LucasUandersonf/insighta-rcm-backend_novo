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


async def test_financial_hole_billings_lists_the_underpriced_line(client, auth_headers_a, admin_engine, tenant_a):
    """A lista real por trás do insight "Você está cobrando menos do que
    devia" (achado do usuário: o card só mostrava o total em R$, não
    QUAIS contas). Sem procedure_name cadastrado no contrato (mesmo
    seed de test_executive_summary_computes_financial_hole_and_margin),
    procedure_label cai pro código TUSS cru."""
    await _seed_revenue_leak_billing(client, admin_engine, tenant_a, auth_headers_a, agreed_value=200.0, charged_value=150.0)
    date_from, date_to = _window()

    response = await client.get(
        f"/api/v1/analytics/financial-hole-billings?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 1
    assert body["total_hole_value"] == 50.0
    item = body["items"][0]
    assert item["patient_full_name"] == "Paciente Analytics"
    assert item["insurance_plan_name"] == "Unimed Nacional"
    assert item["procedure_label"] == "10101012"  # sem nome cadastrado, cai pro código TUSS
    assert item["charged_value"] == 150.0
    assert item["agreed_price"] == 200.0
    assert item["hole_value"] == 50.0


async def test_financial_hole_billings_shows_procedure_name_when_registered(client, admin_engine, tenant_a, auth_headers_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    contract_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.contracts (id, tenant_id, insurance_plan_id, valid_from, status) "
                "VALUES (:id, :t, :plan, '2026-01-01', 'homologado')"
            ),
            {"id": contract_id, "t": tenant_a, "plan": plan_id},
        )
        await conn.execute(
            text(
                "INSERT INTO core.contract_items (tenant_id, contract_id, tuss_code, procedure_name, agreed_price) "
                "VALUES (:t, :contract, :code, :name, :value)"
            ),
            {"t": tenant_a, "contract": contract_id, "code": "10101012", "name": "Consulta em consultório", "value": 200.0},
        )
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Com Nome"}, headers=auth_headers_a)
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
        json={"appointment_id": appointment_resp.json()["id"], "insurance_plan_id": plan_id, "charged_value": 150.0},
        headers=auth_headers_a,
    )
    date_from, date_to = _window()

    response = await client.get(
        f"/api/v1/analytics/financial-hole-billings?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.json()["items"][0]["procedure_label"] == "Consulta em consultório"


async def test_financial_hole_billings_excludes_lines_charged_at_or_above_contract(client, admin_engine, tenant_a, auth_headers_a):
    await _seed_revenue_leak_billing(client, admin_engine, tenant_a, auth_headers_a, agreed_value=200.0, charged_value=200.0)
    date_from, date_to = _window()

    response = await client.get(
        f"/api/v1/analytics/financial-hole-billings?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    body = response.json()
    assert body["total_count"] == 0
    assert body["items"] == []


async def test_financial_hole_billings_isolates_between_tenants(client, admin_engine, tenant_a, auth_headers_a, auth_headers_b):
    await _seed_revenue_leak_billing(client, admin_engine, tenant_a, auth_headers_a, agreed_value=200.0, charged_value=150.0)
    date_from, date_to = _window()

    response_b = await client.get(
        f"/api/v1/analytics/financial-hole-billings?date_from={date_from}&date_to={date_to}", headers=auth_headers_b
    )
    assert response_b.status_code == 200
    assert response_b.json()["total_count"] == 0


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


async def test_agenda_metrics_computes_idle_capacity_revenue_lost(client, auth_headers_a, admin_engine, tenant_a):
    """Grade cobrindo os 7 dias da semana (evita depender de qual weekday
    'hoje' cai em) com bem mais capacidade instalada do que o único
    agendamento criado — sobra ociosidade de sobra para o cálculo ter
    algo a converter em R$ (ver capacity_service.estimate_idle_capacity_revenue_lost)."""
    professional_resp = await client.post(
        "/api/v1/professionals",
        json={
            "full_name": "Dr. Ocioso",
            "availability": [{"weekday": wd, "start_time": "08:00:00", "end_time": "12:00:00"} for wd in range(7)],
        },
        headers=auth_headers_a,
    )
    assert professional_resp.status_code == 201
    professional_id = professional_resp.json()["id"]

    plan_id = await _create_insurance_plan(admin_engine, tenant_a, display_name="Ocioso Saúde", normalized_key="ocioso_saude")
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Ocioso"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "professional_id": professional_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "duration_minutes": 60,
            "procedure_code": "10101012",
        },
        headers=auth_headers_a,
    )
    assert appointment_resp.status_code == 201
    appointment_id = appointment_resp.json()["id"]

    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 200.0},
        headers=auth_headers_a,
    )
    assert billing_resp.status_code == 201

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/agenda-metrics?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    body = response.json()

    # 3 dias * 240 min de grade - 60 min ocupados = 660 min ociosos.
    assert body["total_idle_minutes"] == 660
    # 660 min ociosos / 60 min por consulta = 11 consultas equivalentes * R$ 200 = R$ 2200.
    assert body["estimated_revenue_lost_to_idle_capacity"] == 2200.0


async def test_no_show_risk_breakdown_and_estimate_ignore_the_dashboard_window(client, auth_headers_a, admin_engine, tenant_a):
    """BUG CORRIGIDO (achado via scripts/seed_demo_data.py):
    no_show_risk_breakdown filtrava agendamentos 'scheduled' pela janela
    do DASHBOARD (date_from/date_to, tipicamente uma janela no PASSADO,
    ex: 'últimos 30 dias') — mas um agendamento 'scheduled' É, por
    definição, um evento FUTURO. A interseção era estruturalmente vazia
    em qualquer uso real do produto, zerando estimated_revenue_at_risk e
    o insight de risco de falta sempre que a página era aberta com o
    filtro de período padrão. Este teste consulta agenda-metrics com uma
    janela inteiramente no PASSADO e prova que mesmo assim o risco de um
    agendamento FUTURO aparece."""
    from tests.integration.test_no_show_risk import _insert_past_appointment

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Faltoso Demo"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    # 3 faltas passadas na mesma combinação dia-da-semana+período —
    # mesmo padrão de test_no_show_risk.py — garantindo risco "alto".
    base_monday = date.today() - timedelta(days=date.today().weekday() + 7)
    for weeks_back in range(3):
        past_monday = base_monday - timedelta(weeks=weeks_back)
        scheduled = datetime.combine(past_monday, datetime.min.time(), tzinfo=timezone.utc).replace(hour=14)
        await _insert_past_appointment(admin_engine, tenant_a, patient_id, scheduled, "no_show")

    # Nova consulta futura, mesma combinação dia/período -> nasce "alto".
    days_until_monday = (0 - date.today().weekday()) % 7
    next_monday = date.today() + timedelta(days=days_until_monday or 7)
    future_scheduled = datetime.combine(next_monday, datetime.min.time(), tzinfo=timezone.utc).replace(hour=14)
    future_appt = await client.post(
        "/api/v1/appointments", json={"patient_id": patient_id, "scheduled_at": future_scheduled.isoformat()}, headers=auth_headers_a
    )
    assert future_appt.json()["no_show_risk_level"] == "alto"

    # Faturamento qualquer, só para avg_charged_value não ser zero.
    await _seed_revenue_leak_billing(client, admin_engine, tenant_a, auth_headers_a, agreed_value=200.0, charged_value=200.0)

    # Janela típica de dashboard ("últimos 7 dias", terminando hoje) —
    # não inclui a segunda-feira futura do agendamento de risco criado
    # acima (sempre pelo menos 1 dia à frente).
    date_from = (date.today() - timedelta(days=7)).isoformat()
    date_to = date.today().isoformat()

    response = await client.get(
        f"/api/v1/analytics/agenda-metrics?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    body = response.json()
    risk_counts = {b["level"]: b["count"] for b in body["no_show_risk_breakdown"]}
    assert risk_counts.get("alto", 0) >= 1
    assert body["estimated_revenue_at_risk"] > 0


async def test_smart_insights_flags_financial_hole_from_current_period(client, auth_headers_a, admin_engine, tenant_a):
    await _seed_revenue_leak_billing(client, admin_engine, tenant_a, auth_headers_a, agreed_value=300.0, charged_value=250.0)
    date_from, date_to = _window()

    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    insights = response.json()["insights"]
    assert any(
        i["title"] == "Você está cobrando menos do que devia de alguns convênios" and i["financial_impact"] == 50.0
        for i in insights
    )


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
        "denial-risk-distribution",
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
    assert "No ritmo atual, a meta do ano não vai ser alcançada" not in titles


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
    annual_insight = next((i for i in insights if i["title"] == "No ritmo atual, a meta do ano não vai ser alcançada"), None)
    # Sem nenhum faturamento no ano (banco limpo por teste), o ritmo real
    # é 0% de qualquer ritmo esperado > 0 -> sempre crítico, qualquer que
    # seja a data em que a suíte rodar.
    assert annual_insight is not None
    assert annual_insight["severity"] == "critical"
    # Linguagem sem jargão nem sigla — ver DECISÃO de reescrita em
    # smart_insights_engine.py.
    assert "trazer pacientes novos" in annual_insight["message"] or "reativar" in annual_insight["message"]


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


async def test_agenda_metrics_lists_upcoming_risk_appointments_soonest_first(client, auth_headers_a, admin_engine, tenant_a):
    """Card 'Risco de falta — próximos dias' da Sala de Comando: só
    agendamentos FUTUROS, status 'scheduled', risco médio/alto — nunca
    escopado pela janela de período do dashboard (ver DECISÃO em
    AnalyticsRepository.upcoming_risk_appointments), ordenados do mais
    próximo pro mais distante, e um paciente sem histórico (risco
    indeterminado) nunca aparece na lista."""
    patient_far = (await client.post("/api/v1/patients", json={"full_name": "Paciente Faltoso Distante"}, headers=auth_headers_a)).json()
    patient_soon = (await client.post("/api/v1/patients", json={"full_name": "Paciente Faltoso Próximo"}, headers=auth_headers_a)).json()
    patient_no_history = (await client.post("/api/v1/patients", json={"full_name": "Paciente Sem Histórico"}, headers=auth_headers_a)).json()

    # Histórico de 100% de falta pros dois primeiros pacientes — o
    # suficiente pro motor de risco (no_show_risk_engine.py) classificar
    # o PRÓXIMO agendamento deles como "alto" (rate > 30%, ver
    # _MEDIUM_THRESHOLD/_classify).
    async with admin_engine.begin() as conn:
        for patient in (patient_far, patient_soon):
            for i in range(3):
                await conn.execute(
                    text(
                        "INSERT INTO core.appointments (tenant_id, patient_id, scheduled_at, status) "
                        "VALUES (:t, :p, :dt, 'no_show')"
                    ),
                    {"t": tenant_a, "p": patient["id"], "dt": datetime.now(timezone.utc) - timedelta(days=30 + i)},
                )

    far_at = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    soon_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    no_history_at = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()

    for patient_id, scheduled_at in ((patient_far["id"], far_at), (patient_soon["id"], soon_at), (patient_no_history["id"], no_history_at)):
        resp = await client.post(
            "/api/v1/appointments",
            json={"patient_id": patient_id, "scheduled_at": scheduled_at, "procedure_code": "10101012", "cid_code": "Z00.0"},
            headers=auth_headers_a,
        )
        assert resp.status_code == 201

    response = await client.get("/api/v1/analytics/agenda-metrics", headers=auth_headers_a)
    assert response.status_code == 200
    upcoming = response.json()["upcoming_risk_appointments"]

    names = [item["patient_full_name"] for item in upcoming]
    assert names == ["Paciente Faltoso Próximo", "Paciente Faltoso Distante"]  # soonest first
    assert all(item["risk_level"] == "alto" for item in upcoming)
    assert "Paciente Sem Histórico" not in names  # indeterminado nunca entra na lista


async def test_denial_risk_distribution_counts_by_level(client, auth_headers_a, admin_engine, tenant_a):
    """Donut 'Distribuição de risco de glosa' do Painel: CONTA
    faturamentos por nível (não soma valor, ver denial_risk_value_breakdown
    em test_executive_summary_computes_financial_hole_and_margin) —
    total_reviewed é a soma dos 3 níveis."""
    patient = (await client.post("/api/v1/patients", json={"full_name": "Paciente Distribuição"}, headers=auth_headers_a)).json()
    plan_id = await _create_insurance_plan(admin_engine, tenant_a, display_name="Plano Distribuição", normalized_key="plano_distribuicao")

    # Faltando CID -> regra missing_cid dispara, severidade "high" (ver
    # denial_risk_engine.py::_rule_missing_cid).
    appt_high = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient["id"],
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
        },
        headers=auth_headers_a,
    )
    assert appt_high.status_code == 201
    billing_high = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appt_high.json()["id"], "insurance_plan_id": plan_id, "charged_value": 150.0},
        headers=auth_headers_a,
    )
    assert billing_high.status_code == 201

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/denial-risk-distribution?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    body = response.json()
    counts = {item["level"]: item["count"] for item in body["items"]}
    assert counts.get("high") == 1
    assert body["total_reviewed"] == sum(counts.values())


async def test_agenda_metrics_reports_no_show_rate_per_weekday(client, auth_headers_a, admin_engine, tenant_a):
    """Diferente de weekday_histogram (volume bruto), weekday_no_show_rates
    responde diretamente 'segunda tem taxa de falta X%' — só conta
    atendimentos RESOLVIDOS (completed/no_show), nunca 'scheduled'."""
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Taxa Semanal"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    days_until_monday = (0 - date.today().weekday()) % 7 or 7
    target_monday = date.today() + timedelta(days=days_until_monday)  # garante Python weekday()==0 -> Postgres DOW==1
    monday = datetime.combine(target_monday, datetime.min.time(), tzinfo=timezone.utc)

    async with admin_engine.begin() as conn:
        for status in ("no_show", "completed", "completed"):
            await conn.execute(
                text("INSERT INTO core.appointments (tenant_id, patient_id, scheduled_at, status) VALUES (:t, :p, :dt, :s)"),
                {"t": tenant_a, "p": patient_id, "dt": monday, "s": status},
            )
        # Um 'scheduled' no mesmo dia NÃO deve entrar na conta (sem desfecho ainda).
        await conn.execute(
            text("INSERT INTO core.appointments (tenant_id, patient_id, scheduled_at, status) VALUES (:t, :p, :dt, 'scheduled')"),
            {"t": tenant_a, "p": patient_id, "dt": monday},
        )

    response = await client.get(
        f"/api/v1/analytics/agenda-metrics?date_from={monday.date().isoformat()}&date_to={monday.date().isoformat()}",
        headers=auth_headers_a,
    )
    assert response.status_code == 200
    bucket = next(b for b in response.json()["weekday_no_show_rates"] if b["weekday"] == 1)  # segunda (Python weekday 0 -> DOW 1)
    assert bucket["no_show_count"] == 1
    assert bucket["total_appointments"] == 3  # scheduled excluído
    assert round(bucket["no_show_rate"], 4) == round(1 / 3, 4)


async def test_agenda_metrics_counts_professionals_without_availability(client, auth_headers_a, admin_engine, tenant_a):
    """Todo profissional auto-criado por upload de arquivo nasce sem
    grade — este contador existe pra essa lacuna parar de ser
    silenciosa (ver DECISÃO em AgendaMetricsResponse)."""
    with_grade = await client.post(
        "/api/v1/professionals",
        json={"full_name": "Dr. Com Grade", "availability": [{"weekday": 1, "start_time": "08:00:00", "end_time": "12:00:00"}]},
        headers=auth_headers_a,
    )
    assert with_grade.status_code == 201
    without_grade = await client.post(
        "/api/v1/professionals", json={"full_name": "Dr. Sem Grade", "availability": []}, headers=auth_headers_a
    )
    assert without_grade.status_code == 201

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/agenda-metrics?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    assert response.json()["professionals_without_availability_count"] == 1


async def test_smart_insights_flags_professional_outlier(client, auth_headers_a, admin_engine, tenant_a):
    """Radar de Profissional Fora do Padrão — ponta a ponta: um
    profissional concentrando faturamento de alto risco (CID ausente)
    enquanto outro fatura normalmente deveria virar insight nomeado."""
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_contract(admin_engine, tenant_a, plan_id, procedure_code="10101012", agreed_value=150.0)

    outlier_resp = await client.post("/api/v1/professionals", json={"full_name": "Dr. Radar"}, headers=auth_headers_a)
    outlier_id = outlier_resp.json()["id"]
    normal_resp = await client.post("/api/v1/professionals", json={"full_name": "Dra. Normal"}, headers=auth_headers_a)
    normal_id = normal_resp.json()["id"]

    async def _bill(*, professional_id, with_cid, n):
        for _ in range(n):
            patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Radar"}, headers=auth_headers_a)
            patient_id = patient_resp.json()["id"]
            appt_payload = {
                "patient_id": patient_id,
                "professional_id": professional_id,
                "insurance_plan_id": plan_id,
                "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                "procedure_code": "10101012",
            }
            if with_cid:
                appt_payload["cid_code"] = "J06"
            appt_resp = await client.post("/api/v1/appointments", json=appt_payload, headers=auth_headers_a)
            appointment_id = appt_resp.json()["id"]
            await client.post(
                "/api/v1/billing",
                json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 150.0},
                headers=auth_headers_a,
            )

    # Dr. Radar: 6 faturamentos, todos sem CID -> risco alto em 100%.
    await _bill(professional_id=outlier_id, with_cid=False, n=6)
    # Dra. Normal: 6 faturamentos, todos com CID -> risco baixo.
    await _bill(professional_id=normal_id, with_cid=True, n=6)

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    insights = response.json()["insights"]
    titles = [i["title"] for i in insights]
    assert any("Dr. Radar" in t and "fora do padrão" in t for t in titles)
    assert not any("Dra. Normal" in t for t in titles)

    # Botão de ação real (Sala de Comando 2.0, item 4 do roadmap) — o
    # href precisa levar pro Dr. Radar EXATO via query param, não pra
    # lista geral de /professionals.
    outlier = next(i for i in insights if "Dr. Radar" in i["title"])
    assert outlier["action_href"] == f"/professionals?highlight={outlier_id}"


async def test_smart_insights_flags_network_comparativo_when_gap_is_large(client, auth_headers_a, admin_engine, tenant_a):
    """Comparativo entre clínicas como manchete do feed — ponta a ponta
    (Sala de Comando 2.0, Nível 1): tenant A com taxa de risco alto muito
    acima da mediana de outras 5 clínicas ativas deveria virar insight
    "comparativo", com impacto em R$ projetado sobre o faturamento do
    próprio período (nunca 0, nunca inventado — ver
    build_network_comparativo_insight)."""
    from tests.integration.test_network_benchmark import _insert_tenant, _seed_billing_rows

    await _seed_billing_rows(admin_engine, tenant_a, n=6, risk_level="high")
    # 5 outras clínicas ativas com amostra suficiente, todas com risco
    # baixo -> mediana da rede bem abaixo da taxa de A (mesmo cenário de
    # test_network_benchmark_computes_median_across_other_tenants).
    for i in range(5):
        other = await _insert_tenant(admin_engine, trade_name=f"Clínica Rede Comparativo {i}")
        await _seed_billing_rows(admin_engine, other, n=6, risk_level="low")

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    insights = response.json()["insights"]
    comparativo = next((i for i in insights if i["severity"] == "comparativo"), None)
    assert comparativo is not None
    assert comparativo["is_new"] is True
    assert comparativo["financial_impact"] is not None and comparativo["financial_impact"] > 0
    assert "acima da rede" in comparativo["title"]


async def test_smart_insights_comparativo_never_blames_revenue_leak_as_denial_reason(
    client, auth_headers_a, admin_engine, tenant_a
):
    """Achado do usuário ao documentar o produto: "value_below_contract_revenue_leak"
    (cobrar ABAIXO do contrato) não é motivo de RECUSA — é a própria
    clínica cobrando barato demais (ver DECISÃO em
    smart_insights_engine.is_true_denial_risk_reason). Sem o filtro, o
    "por onde começar" do Comparativo podia apontar esse motivo como se
    fosse causa de glosa. Aqui, o ÚNICO motivo presente é esse — então o
    "por onde começar" precisa ficar ausente, nunca apontar pra ele."""
    from tests.integration.test_network_benchmark import _insert_tenant, _seed_billing_rows

    await _seed_billing_rows(admin_engine, tenant_a, n=6, risk_level="high", reasons=["value_below_contract_revenue_leak"])
    for i in range(5):
        other = await _insert_tenant(admin_engine, trade_name=f"Clínica Rede Revenue Leak {i}")
        await _seed_billing_rows(admin_engine, other, n=6, risk_level="low")

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    insights = response.json()["insights"]
    comparativo = next((i for i in insights if i["severity"] == "comparativo"), None)
    assert comparativo is not None
    assert "é um bom lugar pra começar a corrigir" not in comparativo["message"]
    assert "mais baixo" not in comparativo["message"]


async def test_smart_insights_has_no_comparativo_when_cohort_is_insufficient(client, auth_headers_a, admin_engine, tenant_a):
    """Sem clínicas suficientes na rede (só 1 outra), o Comparativo nunca
    deveria virar insight — mesma garantia de amostra mínima da aba
    Comparativo (ver test_network_benchmark_insufficient_cohort_returns_null),
    agora provada também no feed de insights."""
    from tests.integration.test_network_benchmark import _insert_tenant, _seed_billing_rows

    await _seed_billing_rows(admin_engine, tenant_a, n=6, risk_level="high")
    other = await _insert_tenant(admin_engine, trade_name="Outra Clínica Isolada Comparativo")
    await _seed_billing_rows(admin_engine, other, n=6, risk_level="low")

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    insights = response.json()["insights"]
    assert not any(i["severity"] == "comparativo" for i in insights)


# Achado 5 da Auditoria de Templates e Insights (médio): os 4 insights
# novos (canal de agendamento, motivo de cancelamento, concentração de
# OPME, visibilidade de coparticipação) tinham cobertura de UNIDADE
# excelente (tests/test_smart_insights_engine.py, sem banco), mas
# NENHUMA cobertura de integração contra Postgres real — diferente do
# padrão já estabelecido para os demais insights neste mesmo arquivo. Os
# 4 testes abaixo fecham essa lacuna: provam que a agregação SQL de cada
# um (GROUP BY, filtro de status, JOIN com Appointment) de fato funciona
# contra um banco real, não só que a lógica pura do motor está certa.


async def _create_appointment_direct(admin_engine, tenant_id, patient_id, scheduled_at, **extra_columns):
    """Insere um Appointment direto via SQL — usado quando o campo que o
    teste precisa (booking_channel/cancellation_reason) não é exposto
    pelo endpoint manual POST /appointments (só a ingestão em massa do
    Template de Agenda grava esses campos hoje, ver
    NormalizationService._get_or_create_or_update_appointment_from_agenda).
    Mesmo padrão já usado em test_agenda_metrics_reports_no_show_rate_per_weekday
    acima para status='no_show'/'completed'."""
    columns = ["tenant_id", "patient_id", "scheduled_at", *extra_columns.keys()]
    placeholders = ", ".join(f":{c}" for c in columns)
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(f"INSERT INTO core.appointments ({', '.join(columns)}) VALUES ({placeholders})"),
            {"tenant_id": tenant_id, "patient_id": patient_id, "scheduled_at": scheduled_at, **extra_columns},
        )


async def test_smart_insights_flags_booking_channel_no_show_from_real_data(client, auth_headers_a, admin_engine, tenant_a):
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Canal"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    scheduled_at = datetime.now(timezone.utc) + timedelta(days=1)

    # whatsapp: 6 de 10 faltaram (60%); telefone: 1 de 10 (10%) — média
    # geral 35%, whatsapp fica 25pp acima (crítico, >= 20pp).
    for _ in range(6):
        await _create_appointment_direct(
            admin_engine, tenant_a, patient_id, scheduled_at, status="no_show", booking_channel="whatsapp"
        )
    for _ in range(4):
        await _create_appointment_direct(
            admin_engine, tenant_a, patient_id, scheduled_at, status="completed", booking_channel="whatsapp"
        )
    for _ in range(1):
        await _create_appointment_direct(
            admin_engine, tenant_a, patient_id, scheduled_at, status="no_show", booking_channel="telefone"
        )
    for _ in range(9):
        await _create_appointment_direct(
            admin_engine, tenant_a, patient_id, scheduled_at, status="completed", booking_channel="telefone"
        )

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    titles = [i["title"] for i in response.json()["insights"]]
    assert any("falta mais" in t and "whatsapp" in t.lower() for t in titles)


async def test_smart_insights_flags_cancellation_reason_concentration_from_real_data(
    client, auth_headers_a, admin_engine, tenant_a
):
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Cancelamento"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    scheduled_at = datetime.now(timezone.utc) + timedelta(days=1)

    # 7 de 10 cancelamentos pelo MESMO motivo (70% -> crítico, >= 60%).
    for _ in range(7):
        await _create_appointment_direct(
            admin_engine, tenant_a, patient_id, scheduled_at, status="cancelled", cancellation_reason="Sala em manutenção"
        )
    for _ in range(3):
        await _create_appointment_direct(
            admin_engine, tenant_a, patient_id, scheduled_at, status="cancelled", cancellation_reason="Paciente remarcou"
        )

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    titles = [i["title"] for i in response.json()["insights"]]
    assert any("mesmo motivo" in t and "Sala em manutenção" in t for t in titles)


async def _create_billing_with_item_type(client, auth_headers, plan_id, *, charged_value, item_type=None, coparticipation_value=None):
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Faturamento"}, headers=auth_headers)
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
        headers=auth_headers,
    )
    appointment_id = appointment_resp.json()["id"]
    payload = {"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": charged_value}
    if item_type is not None:
        payload["item_type"] = item_type
    if coparticipation_value is not None:
        payload["coparticipation_value"] = coparticipation_value
    resp = await client.post("/api/v1/billing", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_smart_insights_flags_opme_concentration_from_real_data(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    # OPME = 3000, procedimento = 7000 -> 30% do total, bem acima do piso
    # (15%) e do período anterior vazio (delta = 30pp >= 5pp).
    await _create_billing_with_item_type(client, auth_headers_a, plan_id, charged_value=3000.0, item_type="material_opme")
    await _create_billing_with_item_type(client, auth_headers_a, plan_id, charged_value=7000.0, item_type="procedimento")

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    titles = [i["title"] for i in response.json()["insights"]]
    assert any("OPME" in t for t in titles)


async def test_smart_insights_flags_coparticipation_visibility_from_real_data(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    # 5 faturamentos com coparticipação preenchida (amostra mínima) —
    # período anterior vazio, então é "a primeira vez" (dispara).
    for _ in range(5):
        await _create_billing_with_item_type(client, auth_headers_a, plan_id, charged_value=200.0, coparticipation_value=30.0)

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    titles = [i["title"] for i in response.json()["insights"]]
    assert any("coparticipação" in t.lower() for t in titles)


# "O que resta em aberto" da Auditoria de Templates e Insights: peça
# natural do mesmo padrão que Guia/coparticipação já fecharam —
# core.lotes.status/closed_at (Fase 2) já modelados, sem nenhum insight
# consumindo até esta rodada. Mesmo espírito dos 4 testes acima (Achado
# 5): prova que a agregação SQL (LoteRepository.stale_open_lotes_summary)
# de fato funciona contra um banco real, não só que o motor puro está
# certo (já coberto em test_smart_insights_engine.py).


async def _create_lote_direct(admin_engine, tenant_id, plan_id, *, created_at, status="aberto", tipo="consulta") -> str:
    """Insere um Lote direto via SQL, com created_at controlado — mesmo
    motivo de _create_appointment_direct acima: o endpoint manual
    (POST /lotes) sempre grava created_at = now() do banco (server_default,
    ver app/models/lote.py), sem jeito de simular "criado há 45 dias"
    através da API."""
    lote_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.lotes (id, tenant_id, insurance_plan_id, tipo, status, created_at) "
                "VALUES (:id, :t, :plan, :tipo, :status, :created_at)"
            ),
            {"id": lote_id, "t": tenant_id, "plan": plan_id, "tipo": tipo, "status": status, "created_at": created_at},
        )
    return lote_id


async def test_smart_insights_flags_stale_open_lotes_from_real_data(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    now = datetime.now(timezone.utc)

    # 2 lotes abertos há 45 dias (> corte de 30) — devem contar; o de 50
    # dias é o mais antigo (idade reportada no insight).
    await _create_lote_direct(admin_engine, tenant_a, plan_id, created_at=now - timedelta(days=45))
    await _create_lote_direct(admin_engine, tenant_a, plan_id, created_at=now - timedelta(days=50))
    # Lote aberto há só 5 dias — dentro do corte, não deveria contar.
    await _create_lote_direct(admin_engine, tenant_a, plan_id, created_at=now - timedelta(days=5))
    # Lote FECHADO há 90 dias — status != 'aberto', nunca deveria contar
    # (um lote fechado não está "parado", já virou fatura ou está pronto
    # pra virar).
    await _create_lote_direct(admin_engine, tenant_a, plan_id, created_at=now - timedelta(days=90), status="fechado")

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    insights = response.json()["insights"]
    lote_insight = next((i for i in insights if "lote" in i["title"].lower()), None)
    assert lote_insight is not None
    assert lote_insight["severity"] == "warning"
    assert lote_insight["category"] == "faturamento"
    assert "2 lotes" in lote_insight["message"]
    assert "50 dias" in lote_insight["message"]
    # Sem tela de Lotes no frontend ainda — nunca inventa destino.
    assert lote_insight["action_href"] is None


async def test_smart_insights_absent_when_no_lote_is_stale(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    now = datetime.now(timezone.utc)
    # Aberto há só 5 dias (dentro do corte) e um fechado há bastante
    # tempo — nenhum dos dois deveria disparar o insight.
    await _create_lote_direct(admin_engine, tenant_a, plan_id, created_at=now - timedelta(days=5))
    await _create_lote_direct(admin_engine, tenant_a, plan_id, created_at=now - timedelta(days=90), status="fechado")

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    titles = [i["title"] for i in response.json()["insights"]]
    assert not any("lote" in t.lower() for t in titles)
