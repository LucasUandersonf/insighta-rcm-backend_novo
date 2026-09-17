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
    assert set(denial.keys()) == {
        "key",
        "label",
        "your_rate",
        "your_sample",
        "network_median",
        "cohort_size",
        "cohort_is_segmented_by_specialty",
    }


# ---------------------------------------------------------------------
# "Mapa de Dados Insighta" — pilar Comparativo & rede: cohort segmentado
# por Tenant.specialty quando a amostra segmentada atinge o piso, senão
# cai pro cohort geral.
# ---------------------------------------------------------------------


async def _set_specialty(admin_engine, tenant_id: str, specialty: str | None) -> None:
    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE core.tenants SET specialty = :s WHERE id = :t"), {"s": specialty, "t": tenant_id})


async def test_network_benchmark_segments_cohort_by_specialty_when_enough_sample(
    client, auth_headers_a, admin_engine, tenant_a
):
    await _set_specialty(admin_engine, tenant_a, "odontologia")
    await _seed_billing_rows(admin_engine, tenant_a, n=6, risk_level="high")

    # 5 clínicas da MESMA especialidade, todas risco baixo -> deveriam
    # formar o cohort segmentado (mediana 0.0).
    for i in range(5):
        same_specialty = await _insert_tenant(admin_engine, trade_name=f"Odonto Vizinha {i}")
        await _set_specialty(admin_engine, same_specialty, "odontologia")
        await _seed_billing_rows(admin_engine, same_specialty, n=6, risk_level="low")

    # 5 clínicas de OUTRA especialidade, risco alto -> nunca deveriam
    # entrar na mediana quando o segmento já tem amostra suficiente.
    for i in range(5):
        other_specialty = await _insert_tenant(admin_engine, trade_name=f"Cardio Vizinha {i}")
        await _set_specialty(admin_engine, other_specialty, "cardiologia")
        await _seed_billing_rows(admin_engine, other_specialty, n=6, risk_level="high")

    response = await client.get("/api/v1/analytics/network-benchmark", headers=auth_headers_a)
    assert response.status_code == 200
    denial = next(m for m in response.json()["metrics"] if m["key"] == "denial")
    assert denial["cohort_is_segmented_by_specialty"] is True
    assert denial["cohort_size"] == 5
    assert denial["network_median"] == 0.0  # só as 5 de odontologia (risco baixo) entraram


async def test_network_benchmark_falls_back_to_general_cohort_without_enough_specialty_sample(
    client, auth_headers_a, admin_engine, tenant_a
):
    """Só 2 clínicas da mesma especialidade (< piso de 5) — cai pro
    cohort geral em vez de devolver None por escassez evitável."""
    await _set_specialty(admin_engine, tenant_a, "odontologia")
    await _seed_billing_rows(admin_engine, tenant_a, n=6, risk_level="high")

    for i in range(2):
        same_specialty = await _insert_tenant(admin_engine, trade_name=f"Odonto Rara {i}")
        await _set_specialty(admin_engine, same_specialty, "odontologia")
        await _seed_billing_rows(admin_engine, same_specialty, n=6, risk_level="low")

    for i in range(5):
        other_specialty = await _insert_tenant(admin_engine, trade_name=f"Geral Vizinha {i}")
        await _seed_billing_rows(admin_engine, other_specialty, n=6, risk_level="high")

    response = await client.get("/api/v1/analytics/network-benchmark", headers=auth_headers_a)
    assert response.status_code == 200
    denial = next(m for m in response.json()["metrics"] if m["key"] == "denial")
    assert denial["cohort_is_segmented_by_specialty"] is False
    assert denial["cohort_size"] == 7  # 2 de odontologia + 5 gerais, nenhuma excluída
    assert denial["network_median"] == 1.0


async def test_network_benchmark_uses_general_cohort_without_own_specialty(
    client, auth_headers_a, admin_engine, tenant_a
):
    """Tenant sem specialty preenchida (estado normal da maioria hoje)
    nunca tenta segmentar — usa o cohort geral direto."""
    await _seed_billing_rows(admin_engine, tenant_a, n=6, risk_level="high")
    for i in range(5):
        other = await _insert_tenant(admin_engine, trade_name=f"Clínica Qualquer {i}")
        await _set_specialty(admin_engine, other, "odontologia")
        await _seed_billing_rows(admin_engine, other, n=6, risk_level="low")

    response = await client.get("/api/v1/analytics/network-benchmark", headers=auth_headers_a)
    assert response.status_code == 200
    denial = next(m for m in response.json()["metrics"] if m["key"] == "denial")
    assert denial["cohort_is_segmented_by_specialty"] is False
    assert denial["cohort_size"] == 5
    assert denial["network_median"] == 0.0
