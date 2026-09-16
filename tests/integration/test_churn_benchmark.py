"""
tests/integration/test_churn_benchmark.py

"Equilíbrio Insighta" (Balanced Scorecard, perna Cliente, mecanismo 4) —
comparativo de churn precoce entre clínicas, ponta a ponta via
GET /analytics/network-benchmark (metric "churn"). Mesma arquitetura de
tests/integration/test_network_benchmark.py (denial/no_show): mediana de
OUTRAS clínicas, nunca a própria, piso de amostra mínima respeitado de
verdade.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text


async def _insert_tenant(admin_engine, *, trade_name: str) -> str:
    from tests.conftest import _insert_tenant as _do_insert

    return await _do_insert(admin_engine, trade_name=trade_name)


async def _create_appointment_direct(admin_engine, tenant_id, patient_id, scheduled_at, status="completed"):
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.appointments (tenant_id, patient_id, scheduled_at, status) VALUES (:t, :p, :dt, :s)"),
            {"t": tenant_id, "p": patient_id, "dt": scheduled_at, "s": status},
        )


async def _seed_at_risk_patient(admin_engine, tenant_id: str, *, count: int = 1) -> None:
    """4 consultas a cada 20 dias (intervalo médio calculável), a
    última há 100 dias — 5x o próprio ritmo (>= piso de 2x), mas dentro
    de 365 dias (ainda não "inativo" de verdade). Mesmo padrão de
    test_early_churn_risk_endpoint_flags_patient_well_past_their_own_rhythm
    em test_analytics.py. `count` pacientes distintos, todos no mesmo
    padrão — usado pra atingir o piso de amostra POR CLÍNICA (>= 5
    pacientes qualificados) que a função SQL exige antes da clínica
    contar na mediana da rede (ver DECISÃO em
    053_network_churn_benchmark.sql)."""
    today = datetime.now(timezone.utc)
    for _ in range(count):
        patient_id = str(uuid.uuid4())
        async with admin_engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO core.patients (id, tenant_id, full_name) VALUES (:id, :t, 'Paciente Em Risco')"),
                {"id": patient_id, "t": tenant_id},
            )
        for days_ago in (160, 140, 120, 100):
            await _create_appointment_direct(admin_engine, tenant_id, patient_id, today - timedelta(days=days_ago))


async def _seed_on_rhythm_patient(admin_engine, tenant_id: str, *, count: int = 1) -> None:
    """Mesmo intervalo de 20 dias, mas última consulta há só 15 dias —
    dentro do próprio ritmo, não deveria contar como em risco. `count`
    pacientes distintos — mesmo motivo de _seed_at_risk_patient acima."""
    today = datetime.now(timezone.utc)
    for _ in range(count):
        patient_id = str(uuid.uuid4())
        async with admin_engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO core.patients (id, tenant_id, full_name) VALUES (:id, :t, 'Paciente No Ritmo')"),
                {"id": patient_id, "t": tenant_id},
            )
        for days_ago in (55, 35, 15):
            await _create_appointment_direct(admin_engine, tenant_id, patient_id, today - timedelta(days=days_ago))


async def test_churn_benchmark_insufficient_cohort_returns_null(client, auth_headers_a, admin_engine, tenant_a):
    await _seed_at_risk_patient(admin_engine, tenant_a)
    # Só 1 outra clínica com amostra suficiente (abaixo do piso de
    # clínicas, que é 5 — mesmo que ELA tenha pacientes suficientes).
    other = await _insert_tenant(admin_engine, trade_name="Outra Clínica Isolada")
    await _seed_on_rhythm_patient(admin_engine, other, count=5)

    response = await client.get("/api/v1/analytics/network-benchmark", headers=auth_headers_a)
    assert response.status_code == 200
    churn = next(m for m in response.json()["metrics"] if m["key"] == "churn")
    assert churn["label"] == "Churn precoce"
    assert churn["your_rate"] == 1.0  # o único paciente qualificado está em risco
    assert churn["network_median"] is None
    assert churn["cohort_size"] < 5


async def test_churn_benchmark_computes_median_across_other_tenants(client, auth_headers_a, admin_engine, tenant_a):
    await _seed_at_risk_patient(admin_engine, tenant_a)

    # 5 outras clínicas ativas, cada uma com 5 pacientes qualificados
    # DENTRO do próprio ritmo (rate 0.0) -> mediana da rede deveria ser
    # 0.0, bem diferente da taxa da própria clínica A (1.0).
    for i in range(5):
        other = await _insert_tenant(admin_engine, trade_name=f"Clínica Rede {i}")
        await _seed_on_rhythm_patient(admin_engine, other, count=5)

    response = await client.get("/api/v1/analytics/network-benchmark", headers=auth_headers_a)
    assert response.status_code == 200
    churn = next(m for m in response.json()["metrics"] if m["key"] == "churn")
    assert churn["your_rate"] == 1.0
    assert churn["cohort_size"] == 5
    assert churn["network_median"] == 0.0


async def test_churn_benchmark_never_leaks_a_single_other_tenants_rate(client, auth_headers_a, admin_engine, tenant_a):
    await _seed_on_rhythm_patient(admin_engine, tenant_a)
    for i in range(5):
        other = await _insert_tenant(admin_engine, trade_name=f"Clínica Vizinha {i}")
        await _seed_at_risk_patient(admin_engine, other, count=5)

    response = await client.get("/api/v1/analytics/network-benchmark", headers=auth_headers_a)
    assert response.status_code == 200
    churn = next(m for m in response.json()["metrics"] if m["key"] == "churn")
    assert churn["network_median"] == 1.0
    assert set(churn.keys()) == {
        "key",
        "label",
        "your_rate",
        "your_sample",
        "network_median",
        "cohort_size",
        "cohort_is_segmented_by_specialty",
    }


async def test_churn_benchmark_segments_cohort_by_specialty_when_enough_sample(
    client, auth_headers_a, admin_engine, tenant_a
):
    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE core.tenants SET specialty = 'odontologia' WHERE id = :t"), {"t": tenant_a})
    await _seed_at_risk_patient(admin_engine, tenant_a)

    for i in range(5):
        same_specialty = await _insert_tenant(admin_engine, trade_name=f"Odonto Vizinha {i}")
        async with admin_engine.begin() as conn:
            await conn.execute(
                text("UPDATE core.tenants SET specialty = 'odontologia' WHERE id = :t"), {"t": same_specialty}
            )
        await _seed_on_rhythm_patient(admin_engine, same_specialty, count=5)

    for i in range(5):
        other_specialty = await _insert_tenant(admin_engine, trade_name=f"Cardio Vizinha {i}")
        async with admin_engine.begin() as conn:
            await conn.execute(
                text("UPDATE core.tenants SET specialty = 'cardiologia' WHERE id = :t"), {"t": other_specialty}
            )
        await _seed_at_risk_patient(admin_engine, other_specialty, count=5)

    response = await client.get("/api/v1/analytics/network-benchmark", headers=auth_headers_a)
    assert response.status_code == 200
    churn = next(m for m in response.json()["metrics"] if m["key"] == "churn")
    assert churn["cohort_is_segmented_by_specialty"] is True
    assert churn["cohort_size"] == 5
    assert churn["network_median"] == 0.0
