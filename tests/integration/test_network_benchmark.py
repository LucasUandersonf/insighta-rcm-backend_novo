"""
tests/integration/test_network_benchmark.py

Comparativo entre clínicas (Sala de Comando 2.0) — prova ponta a ponta
que a função SECURITY DEFINER agrega direito (mediana de OUTRAS clínicas,
nunca a própria, nunca uma linha por clínica) e que o piso de amostra
mínima (min_cohort) é respeito de verdade: sem clínicas suficientes na
base, o comparativo devolve None, nunca um número inventado.
"""
import uuid

from sqlalchemy import text


async def _seed_billing_rows(admin_engine, tenant_id: str, *, n: int, risk_level: str, reasons: list[str] | None = None) -> None:
    """Seeding direto via SQL (bypassa API) — só precisamos de linhas
    reais em core.billing/core.appointments com o denial_risk_level
    desejado, não do fluxo de negócio inteiro. `reasons` opcional
    (default []) — só usado pelo teste do "por onde começar" do
    Comparativo, que precisa de denial_reasons preenchido de verdade
    (ver AnalyticsRepository.denial_findings_by_plan)."""
    import json

    patient_id = str(uuid.uuid4())
    plan_id = str(uuid.uuid4())
    reasons_json = json.dumps(reasons or [])
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.patients (id, tenant_id, full_name) VALUES (:id, :t, 'Paciente Benchmark')"),
            {"id": patient_id, "t": tenant_id},
        )
        await conn.execute(
            text(
                "INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) "
                "VALUES (:id, :t, 'Convênio Benchmark', 'convenio_benchmark')"
            ),
            {"id": plan_id, "t": tenant_id},
        )
        for _ in range(n):
            appointment_id = str(uuid.uuid4())
            await conn.execute(
                text(
                    "INSERT INTO core.appointments (id, tenant_id, patient_id, scheduled_at, status) "
                    "VALUES (:id, :t, :p, now(), 'completed')"
                ),
                {"id": appointment_id, "t": tenant_id, "p": patient_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO core.billing (tenant_id, appointment_id, insurance_plan_id, charged_value, denial_risk_level, denial_reasons) "
                    "VALUES (:t, :a, :plan, 100.0, :risk, CAST(:reasons AS jsonb))"
                ),
                {"t": tenant_id, "a": appointment_id, "plan": plan_id, "risk": risk_level, "reasons": reasons_json},
            )


async def _insert_tenant(admin_engine, *, trade_name: str) -> str:
    from tests.conftest import _insert_tenant as _do_insert

    return await _do_insert(admin_engine, trade_name=trade_name)


async def test_network_benchmark_insufficient_cohort_returns_null(client, auth_headers_a, admin_engine, tenant_a):
    await _seed_billing_rows(admin_engine, tenant_a, n=6, risk_level="high")
    # Só 1 outra clínica com amostra (abaixo do piso de 5) — mediana da
    # rede não deveria aparecer.
    other = await _insert_tenant(admin_engine, trade_name="Outra Clínica Isolada")
    await _seed_billing_rows(admin_engine, other, n=6, risk_level="low")

    response = await client.get("/api/v1/analytics/network-benchmark", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    denial = next(m for m in body["metrics"] if m["key"] == "denial")
    assert denial["your_rate"] == 1.0  # 100% em risco alto
    assert denial["network_median"] is None
    assert denial["cohort_size"] < 5


async def test_network_benchmark_computes_median_across_other_tenants(client, auth_headers_a, admin_engine, tenant_a):
    # Tenant A: 100% de risco alto (todas as 6 linhas).
    await _seed_billing_rows(admin_engine, tenant_a, n=6, risk_level="high")

    # 5 outras clínicas ativas, cada uma com amostra suficiente (>=5),
    # todas com risco BAIXO (0%) -> mediana da rede deveria ser 0.0,
    # bem diferente da taxa da própria clínica A.
    for i in range(5):
        other = await _insert_tenant(admin_engine, trade_name=f"Clínica Rede {i}")
        await _seed_billing_rows(admin_engine, other, n=6, risk_level="low")

    response = await client.get("/api/v1/analytics/network-benchmark", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    denial = next(m for m in body["metrics"] if m["key"] == "denial")
    assert denial["your_rate"] == 1.0
    assert denial["cohort_size"] == 5
    assert denial["network_median"] == 0.0  # nenhuma das 5 outras tem risco alto/médio


async def test_network_benchmark_never_leaks_a_single_other_tenants_rate(client, auth_headers_a, admin_engine, tenant_a):
    """Com cohort exatamente no piso (5), o retorno é só a mediana
    agregada — nunca uma lista, nunca uma taxa "extra" identificável de
    uma clínica específica. Prova indireta: a resposta HTTP só tem os
    campos agregados definidos no schema, nada por-clínica."""
    await _seed_billing_rows(admin_engine, tenant_a, n=6, risk_level="low")
    for i in range(5):
        other = await _insert_tenant(admin_engine, trade_name=f"Clínica Vizinha {i}")
        await _seed_billing_rows(admin_engine, other, n=6, risk_level="high")

    response = await client.get("/api/v1/analytics/network-benchmark", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    denial = next(m for m in body["metrics"] if m["key"] == "denial")
    assert denial["network_median"] == 1.0
    # Só as chaves agregadas do schema — nenhuma lista, nenhum tenant_id de terceiro.
    assert set(denial.keys()) == {"key", "label", "your_rate", "your_sample", "network_median", "cohort_size"}
