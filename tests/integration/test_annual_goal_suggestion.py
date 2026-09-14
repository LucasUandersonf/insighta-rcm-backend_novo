"""
tests/integration/test_annual_goal_suggestion.py

GET /tenant/annual-goal/suggested — Épico F3.3 do Plano Diretor ("Metas
e cenários orientados a dados"). Mesmo espírito de
test_network_benchmark.py: prova que a função SECURITY DEFINER agrega
direito (mediana de OUTRAS clínicas, nunca a própria) e que o piso de
amostra mínima é respeitado de verdade.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text


async def _insert_tenant(admin_engine, *, trade_name: str) -> str:
    from tests.conftest import _insert_tenant as _do_insert

    return await _do_insert(admin_engine, trade_name=trade_name)


async def _seed_billing_at(admin_engine, tenant_id: str, *, charged_value: float, months_ago: float) -> None:
    """1 billing com `created_at` deslocado `months_ago` meses atrás
    (aproximado por dias * 30, suficiente pra cair dentro/fora das
    janelas de 12/24 meses da função SQL, que também usa INTERVAL de
    meses reais — a folga é grande o bastante pra não haver ambiguidade
    de borda em nenhum teste aqui)."""
    patient_id = str(uuid.uuid4())
    plan_id = str(uuid.uuid4())
    appointment_id = str(uuid.uuid4())
    # normalized_key único por chamada (tenant, plano) — evita colidir
    # com a constraint UNIQUE quando o mesmo tenant chama esta função
    # mais de uma vez (trailing + prior period).
    normalized_key = f"convenio_meta_anual_{plan_id[:8]}"
    created_at = datetime.now(timezone.utc) - timedelta(days=months_ago * 30)
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.patients (id, tenant_id, full_name) VALUES (:id, :t, 'Paciente Meta Anual')"),
            {"id": patient_id, "t": tenant_id},
        )
        await conn.execute(
            text(
                "INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) "
                "VALUES (:id, :t, 'Convênio Meta Anual', :key)"
            ),
            {"id": plan_id, "t": tenant_id, "key": normalized_key},
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
                "INSERT INTO core.billing (tenant_id, appointment_id, insurance_plan_id, charged_value, created_at) "
                "VALUES (:t, :a, :plan, :value, :created_at)"
            ),
            {"t": tenant_id, "a": appointment_id, "plan": plan_id, "value": charged_value, "created_at": created_at},
        )


async def test_annual_goal_suggestion_computes_own_growth_rate(client, auth_headers_a, admin_engine, tenant_a):
    # 12 meses anteriores: R$ 1.000. Últimos 12 meses: R$ 1.500 -> +50%.
    await _seed_billing_at(admin_engine, tenant_a, charged_value=1000.0, months_ago=18)
    await _seed_billing_at(admin_engine, tenant_a, charged_value=1500.0, months_ago=3)

    response = await client.get("/api/v1/tenant/annual-goal/suggested", headers=auth_headers_a)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["trailing_12_months_total"] == 1500.0
    assert body["own_growth_rate"] == 0.5
    assert body["own_trend_suggested_goal"] == 2250.0
    # Sem cohort de outras clínicas -> sugestão de rede indefinida.
    assert body["network_growth_median"] is None
    assert body["network_pace_suggested_goal"] is None
    assert body["network_cohort_size"] == 0


async def test_annual_goal_suggestion_none_without_prior_period_data(client, auth_headers_a, admin_engine, tenant_a):
    # Só faturamento RECENTE, nada nos 12 meses anteriores -> taxa própria indefinida.
    await _seed_billing_at(admin_engine, tenant_a, charged_value=800.0, months_ago=2)

    response = await client.get("/api/v1/tenant/annual-goal/suggested", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["trailing_12_months_total"] == 800.0
    assert body["own_growth_rate"] is None
    assert body["own_trend_suggested_goal"] is None


async def test_annual_goal_suggestion_computes_network_median_with_enough_cohort(
    client, auth_headers_a, admin_engine, tenant_a
):
    # Tenant A: sem crescimento próprio calculável (só recente) -> a
    # sugestão de rede precisa aparecer mesmo assim (fontes independentes).
    await _seed_billing_at(admin_engine, tenant_a, charged_value=1000.0, months_ago=2)

    # 5 outras clínicas ativas, cada uma com +100% de crescimento
    # (prior=500, trailing=1000) -> mediana da rede = 1.0 (100%).
    for i in range(5):
        other = await _insert_tenant(admin_engine, trade_name=f"Clínica Meta Anual {i}")
        await _seed_billing_at(admin_engine, other, charged_value=500.0, months_ago=18)
        await _seed_billing_at(admin_engine, other, charged_value=1000.0, months_ago=3)

    response = await client.get("/api/v1/tenant/annual-goal/suggested", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["network_cohort_size"] == 5
    assert body["network_growth_median"] == 1.0
    # trailing (1000) * (1 + 1.0) = 2000.
    assert body["network_pace_suggested_goal"] == 2000.0
    # Independente: sem prior period próprio, a sugestão OWN continua None.
    assert body["own_growth_rate"] is None
    assert body["own_trend_suggested_goal"] is None


async def test_annual_goal_suggestion_never_leaks_a_single_other_tenants_growth(
    client, auth_headers_a, admin_engine, tenant_a
):
    await _seed_billing_at(admin_engine, tenant_a, charged_value=1000.0, months_ago=2)
    for i in range(5):
        other = await _insert_tenant(admin_engine, trade_name=f"Clínica Meta Anual Vizinha {i}")
        await _seed_billing_at(admin_engine, other, charged_value=200.0, months_ago=18)
        await _seed_billing_at(admin_engine, other, charged_value=400.0, months_ago=3)

    response = await client.get("/api/v1/tenant/annual-goal/suggested", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    # Só os campos agregados do schema — nenhuma lista, nenhum tenant_id de terceiro.
    assert set(body.keys()) == {
        "trailing_12_months_total",
        "own_growth_rate",
        "own_trend_suggested_goal",
        "network_growth_median",
        "network_pace_suggested_goal",
        "network_cohort_size",
    }


async def test_atendimento_can_view_annual_goal_suggestion(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao@annual-goal-suggestion.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.get("/api/v1/tenant/annual-goal/suggested", headers=headers)
    assert response.status_code == 200
