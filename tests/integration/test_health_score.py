"""
tests/integration/test_health_score.py

Ponta a ponta via HTTP para a Nota de Saúde Financeira — prova que
AnalyticsService.get_health_score de fato agrega dado real do banco
(componente de amostra insuficiente excluído, RLS isola entre tenants).
O motor puro (regras de score) já é coberto sem banco em
tests/test_health_score_engine.py.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text


def _month_back_date(months_back: int) -> datetime:
    """Um dia (dia 10, meio do mês, nunca vira o mês por engano) dentro do
    mês `months_back` meses atrás do mês corrente — mesma convenção de
    app.services.analytics_service._preceding_month_bounds, reimplementada
    aqui pra semear histórico mensal sem importar uma função privada do
    service."""
    today = date.today()
    year, month = today.year, today.month - months_back
    while month <= 0:
        month += 12
        year -= 1
    return datetime(year, month, 10, 12, 0, tzinfo=timezone.utc)


async def _create_professional(admin_engine, tenant_id, *, specialty: str) -> str:
    professional_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.professionals (id, tenant_id, full_name, specialty) VALUES (:id, :t, :name, :specialty)"),
            {"id": professional_id, "t": tenant_id, "name": f"Dr(a). {specialty}", "specialty": specialty},
        )
    return professional_id


async def _seed_appointments(admin_engine, tenant_id, patient_id, professional_id, *, scheduled_at, no_show_count, completed_count):
    async with admin_engine.begin() as conn:
        for _ in range(no_show_count):
            await conn.execute(
                text(
                    "INSERT INTO core.appointments (tenant_id, patient_id, professional_id, scheduled_at, status) "
                    "VALUES (:t, :p, :prof, :dt, 'no_show')"
                ),
                {"t": tenant_id, "p": patient_id, "prof": professional_id, "dt": scheduled_at},
            )
        for _ in range(completed_count):
            await conn.execute(
                text(
                    "INSERT INTO core.appointments (tenant_id, patient_id, professional_id, scheduled_at, status) "
                    "VALUES (:t, :p, :prof, :dt, 'completed')"
                ),
                {"t": tenant_id, "p": patient_id, "prof": professional_id, "dt": scheduled_at},
            )


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Unimed Nacional", normalized_key="unimed_nacional") -> str:
    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :name, :key)"),
            {"id": plan_id, "t": tenant_id, "name": display_name, "key": normalized_key},
        )
    return plan_id


async def test_health_score_is_none_for_tenant_without_any_data(client, auth_headers_a):
    response = await client.get("/api/v1/analytics/health-score", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["score"] is None
    assert body["components"] == []
    assert body["window_days"] == 90


async def test_health_score_reflects_high_risk_billing(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Nota de Saúde"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
            # cid_code omitido -> billing nasce com denial_risk_level "high"
        },
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]
    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 150.0},
        headers=auth_headers_a,
    )
    assert billing_resp.json()["denial_risk_level"] == "high"

    response = await client.get("/api/v1/analytics/health-score", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["score"] is not None
    denial_component = next(c for c in body["components"] if c["key"] == "denial")
    assert denial_component["rate"] == 1.0  # 100% do faturamento no período está em risco alto
    assert denial_component["sub_score"] == 0.0  # acima do teto (25%) -> pior nota possível
    # Sem agendamento resolvido nem recurso decidido no período -> só o componente de glosa entra.
    assert {c["key"] for c in body["components"]} == {"denial"}


async def test_health_score_no_show_component_uses_resolved_appointments_only(client, auth_headers_a, admin_engine, tenant_a):
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Falta"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    now = datetime.now(timezone.utc) - timedelta(days=1)

    async with admin_engine.begin() as conn:
        for status in ("no_show", "completed", "completed", "completed"):
            await conn.execute(
                text("INSERT INTO core.appointments (tenant_id, patient_id, scheduled_at, status) VALUES (:t, :p, :dt, :s)"),
                {"t": tenant_a, "p": patient_id, "dt": now, "s": status},
            )
        # 'scheduled' não deveria contar (sem desfecho ainda).
        await conn.execute(
            text("INSERT INTO core.appointments (tenant_id, patient_id, scheduled_at, status) VALUES (:t, :p, :dt, 'scheduled')"),
            {"t": tenant_a, "p": patient_id, "dt": now},
        )

    response = await client.get("/api/v1/analytics/health-score", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    no_show_component = next(c for c in body["components"] if c["key"] == "no_show")
    assert round(no_show_component["rate"], 4) == round(1 / 4, 4)  # 1 falta em 4 resolvidos


async def test_health_score_appeal_component_needs_minimum_sample(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Recurso"}, headers=auth_headers_a)
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
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]
    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 100.0},
        headers=auth_headers_a,
    )
    billing_id = billing_resp.json()["id"]

    # Só 2 recursos decididos (abaixo do mínimo de 3, ver _MIN_APPEAL_SAMPLE)
    # -> componente "appeal" não deveria aparecer.
    now = datetime.now(timezone.utc)
    async with admin_engine.begin() as conn:
        for status in ("deferido", "indeferido"):
            await conn.execute(
                text(
                    "INSERT INTO core.denial_appeals "
                    "(tenant_id, billing_id, appeal_type, denied_at, deadline_at, status, resolved_at) "
                    "VALUES (:t, :b, 'administrativa', :d, :dl, :s, :r)"
                ),
                {"t": tenant_a, "b": billing_id, "d": now.date(), "dl": (now + timedelta(days=30)).date(), "s": status, "r": now},
            )

    response = await client.get("/api/v1/analytics/health-score", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert "appeal" not in {c["key"] for c in body["components"]}


async def _insert_health_score_snapshot(admin_engine, tenant_id: str, *, snapshot_month, score) -> None:
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.health_score_snapshots (tenant_id, snapshot_month, score) "
                "VALUES (:t, :m, :s)"
            ),
            {"t": tenant_id, "m": snapshot_month, "s": score},
        )


async def test_health_score_has_no_trend_without_any_reference_snapshot(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Sem Histórico"}, headers=auth_headers_a)
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
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]
    await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 100.0},
        headers=auth_headers_a,
    )

    response = await client.get("/api/v1/analytics/health-score", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["score"] is not None
    assert body["trend"] is None  # base nova, sem 3 meses de histórico ainda


async def test_health_score_trend_compares_against_reference_snapshot(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Tendência"}, headers=auth_headers_a)
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
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]
    await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 100.0},
        headers=auth_headers_a,
    )

    reference_month = (date.today() - timedelta(days=95)).replace(day=1)
    await _insert_health_score_snapshot(admin_engine, tenant_a, snapshot_month=reference_month, score=40.0)

    response = await client.get("/api/v1/analytics/health-score", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["score"] is not None
    assert body["trend"] is not None
    assert body["trend"]["reference_score"] == 40.0
    assert body["trend"]["reference_month"] == reference_month.isoformat()
    assert body["trend"]["delta"] == pytest.approx(body["score"] - 40.0)


async def test_health_score_trend_picks_the_most_recent_snapshot_at_or_before_the_reference_mark(
    client, auth_headers_a, admin_engine, tenant_a
):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Vários Snapshots"}, headers=auth_headers_a)
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
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]
    await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 100.0},
        headers=auth_headers_a,
    )

    # Um snapshot antigo o suficiente (referência correta) e um mais
    # recente que NÃO deveria ser escolhido (mais próximo de hoje que a
    # marca de ~90 dias, ainda "no futuro" em relação à referência).
    old_month = (date.today() - timedelta(days=200)).replace(day=1)
    recent_month = (date.today() - timedelta(days=30)).replace(day=1)
    await _insert_health_score_snapshot(admin_engine, tenant_a, snapshot_month=old_month, score=10.0)
    await _insert_health_score_snapshot(admin_engine, tenant_a, snapshot_month=recent_month, score=90.0)

    response = await client.get("/api/v1/analytics/health-score", headers=auth_headers_a)
    assert response.status_code == 200
    trend = response.json()["trend"]
    assert trend["reference_score"] == 10.0


async def test_health_score_trend_ignores_reference_snapshot_with_null_score(
    client, auth_headers_a, admin_engine, tenant_a
):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Snapshot Nulo"}, headers=auth_headers_a)
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
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]
    await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 100.0},
        headers=auth_headers_a,
    )

    reference_month = (date.today() - timedelta(days=95)).replace(day=1)
    # Amostra insuficiente naquele mês -> score gravado como NULL. Nunca
    # deveria virar uma tendência "contra zero".
    await _insert_health_score_snapshot(admin_engine, tenant_a, snapshot_month=reference_month, score=None)

    response = await client.get("/api/v1/analytics/health-score", headers=auth_headers_a)
    assert response.status_code == 200
    assert response.json()["trend"] is None


async def test_health_score_uses_custom_denial_ceiling_configured_on_tenant(client, auth_headers_a, admin_engine, tenant_a):
    """Épico F2.1 do Plano Diretor ("Calibração por especialidade/porte")
    — prova que AnalyticsService.get_health_score de fato resolve e usa o
    teto configurado no tenant, não só a constante fixa do módulo."""
    from tests.integration.test_analytics import _create_contract

    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Teto Customizado"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    # Uma linha de alto risco (300) + uma de baixo risco (700), esta com
    # CID + tabela de contrato cadastrada casando o valor cobrado (senão
    # ela também cairia em "medium" por falta de referência contratual,
    # ver denial_risk_engine._rule_no_contract_reference) -> 30% do
    # faturamento em risco médio/alto. Acima do teto default (25%) ->
    # pior nota possível; abaixo de um teto customizado mais folgado ->
    # nota positiva. Field exige ceiling < 1, então usar 100% de risco
    # (como no resto do arquivo) nunca provaria a diferença — qualquer
    # teto abaixo de 1.0 ainda classificaria 100% como "no teto ou acima".
    await _create_contract(admin_engine, tenant_a, plan_id, procedure_code="10101012", agreed_value=700.0)
    high_risk_appt = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
            # cid_code omitido -> billing nasce com denial_risk_level "high"
        },
        headers=auth_headers_a,
    )
    await client.post(
        "/api/v1/billing",
        json={"appointment_id": high_risk_appt.json()["id"], "insurance_plan_id": plan_id, "charged_value": 300.0},
        headers=auth_headers_a,
    )
    low_risk_appt = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )
    await client.post(
        "/api/v1/billing",
        json={"appointment_id": low_risk_appt.json()["id"], "insurance_plan_id": plan_id, "charged_value": 700.0},
        headers=auth_headers_a,
    )

    # Com o teto default (25%), 30% de risco já é a pior nota possível.
    default_response = await client.get("/api/v1/analytics/health-score", headers=auth_headers_a)
    default_denial = next(c for c in default_response.json()["components"] if c["key"] == "denial")
    assert default_denial["rate"] == pytest.approx(0.30)
    assert default_denial["sub_score"] == 0.0

    # Com um teto customizado mais folgado, o MESMO dado gera nota positiva.
    patch_resp = await client.patch(
        "/api/v1/tenant", json={"health_score_denial_ceiling": 0.60}, headers=auth_headers_a
    )
    assert patch_resp.status_code == 200, patch_resp.text

    response = await client.get("/api/v1/analytics/health-score", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    denial_component = next(c for c in body["components"] if c["key"] == "denial")
    assert denial_component["rate"] == pytest.approx(0.30)
    assert denial_component["sub_score"] > 0.0  # já não é mais a pior nota possível


async def test_health_score_isolates_between_tenants(client, auth_headers_a, auth_headers_b, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Isolamento"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
        },
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]
    await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 150.0},
        headers=auth_headers_a,
    )

    response_b = await client.get("/api/v1/analytics/health-score", headers=auth_headers_b)
    assert response_b.status_code == 200
    # Tenant B não tem nenhum dado próprio -> nunca deveria enxergar o
    # billing de risco alto do tenant A (RLS).
    assert response_b.json()["score"] is None


# ---------------------------------------------------------------------
# "Junta Técnica Insighta" — teto de falta calibrado por especialidade
# DENTRO do mesmo tenant (ver DECISÃO completa em
# health_score_engine.resolve_no_show_ceiling_for_period). O motor puro já
# é coberto sem banco em test_health_score_engine.py — este teste prova a
# FIAÇÃO ponta a ponta: que AnalyticsService.get_health_score de fato
# busca a quebra por especialidade e o histórico mensal reais do banco.
# ---------------------------------------------------------------------


async def test_health_score_no_show_ceiling_is_calibrated_by_specialty_mix(client, auth_headers_a, admin_engine, tenant_a):
    """
    Pneumologia (baixa falta histórica, 10%) domina o volume do período
    (80 de 100 atendimentos); Urologia (falta histórica alta, 30%) é
    minoria (20 de 100). A taxa de falta OBSERVADA no período é 20% —
    abaixo do teto fixo de 40% (nota positiva, sub_score 50 se o teto
    continuasse fixo), mas ACIMA do teto ponderado pela mistura real desta
    clínica (0,10*80 + 0,30*20) / 100 = 0,14 -> sub_score deveria cair pra
    0 (pior nota possível), exatamente o "risco real que passava batido"
    citado pela junta técnica pra uma clínica de especialidade de baixa
    falta.
    """
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Multiespecialidade"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    pneumologia_id = await _create_professional(admin_engine, tenant_a, specialty="Pneumologia")
    urologia_id = await _create_professional(admin_engine, tenant_a, specialty="Urologia")

    # Histórico dos últimos 6 meses FECHADOS fora da janela de 90 dias do
    # score atual (meses 7 a 12 atrás) — 10%/mês pra Pneumologia, 30%/mês
    # pra Urologia, cada um com amostra suficiente (10 atendimentos/mês
    # >= MIN_MONTHS_FOR_CEILING_SUGGESTION em número de MESES, não de
    # atendimentos por mês).
    for months_back in range(7, 13):
        when = _month_back_date(months_back)
        await _seed_appointments(admin_engine, tenant_a, patient_id, pneumologia_id, scheduled_at=when, no_show_count=1, completed_count=9)
        await _seed_appointments(admin_engine, tenant_a, patient_id, urologia_id, scheduled_at=when, no_show_count=3, completed_count=7)

    # Período atual (dentro da janela de 90 dias) — mesma taxa agregada de
    # 20% em ambas as especialidades, só o VOLUME é bem desbalanceado.
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    await _seed_appointments(admin_engine, tenant_a, patient_id, pneumologia_id, scheduled_at=yesterday, no_show_count=16, completed_count=64)
    await _seed_appointments(admin_engine, tenant_a, patient_id, urologia_id, scheduled_at=yesterday, no_show_count=4, completed_count=16)

    response = await client.get("/api/v1/analytics/health-score", headers=auth_headers_a)
    assert response.status_code == 200
    no_show_component = next(c for c in response.json()["components"] if c["key"] == "no_show")
    assert no_show_component["rate"] == pytest.approx(0.20)  # taxa agregada continua a mesma de sempre
    assert no_show_component["sub_score"] == 0.0  # mas o teto ponderado por especialidade já a considera pior caso


async def test_health_score_no_show_ceiling_stays_flat_for_single_specialty_clinic(client, auth_headers_a, admin_engine, tenant_a):
    """Controle: SÓ Pneumologia no período (sem mistura de
    especialidade) -> o teto continua o default fixo de 40%, mesmo
    comportamento de antes desta calibração (a maioria das clínicas é de
    especialidade única)."""
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Especialidade Única"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    pneumologia_id = await _create_professional(admin_engine, tenant_a, specialty="Pneumologia")

    for months_back in range(7, 13):
        when = _month_back_date(months_back)
        await _seed_appointments(admin_engine, tenant_a, patient_id, pneumologia_id, scheduled_at=when, no_show_count=1, completed_count=9)

    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    await _seed_appointments(admin_engine, tenant_a, patient_id, pneumologia_id, scheduled_at=yesterday, no_show_count=20, completed_count=80)

    response = await client.get("/api/v1/analytics/health-score", headers=auth_headers_a)
    assert response.status_code == 200
    no_show_component = next(c for c in response.json()["components"] if c["key"] == "no_show")
    assert no_show_component["rate"] == pytest.approx(0.20)
    assert no_show_component["sub_score"] == pytest.approx(50.0)  # 100*(1 - 0.20/0.40), teto default inalterado
