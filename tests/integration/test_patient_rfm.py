"""
tests/integration/test_patient_rfm.py

RFM completo (Gaps Dossiê Insighta RCM, item 4) — Recência, Frequência
e Valor por paciente, segmentados (ver DECISÃO completa em
app/services/rfm_engine.py). Prova ponta a ponta a classificação em
segmentos com uma base de 5 pacientes desenhada pra cair, cada um, num
segmento diferente e distinguível (mesmo espírito de
test_inactive_patients.py: histórico real via SQL direto, sem passar
pelos limiares no vácuo).
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text


async def _create_insurance_plan(admin_engine, tenant_id: str) -> str:
    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) "
                "VALUES (:id, :t, 'Plano RFM', 'plano_rfm')"
            ),
            {"id": plan_id, "t": tenant_id},
        )
    return plan_id


async def _create_appointment(
    admin_engine, tenant_id: str, patient_id: str, scheduled_at: datetime, *, plan_id: str | None = None, charged_value: float | None = None
) -> None:
    appointment_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.appointments (id, tenant_id, patient_id, scheduled_at, status) "
                "VALUES (:id, :t, :p, :dt, 'completed')"
            ),
            {"id": appointment_id, "t": tenant_id, "p": patient_id, "dt": scheduled_at},
        )
        if charged_value is not None:
            await conn.execute(
                text(
                    "INSERT INTO core.billing (tenant_id, appointment_id, insurance_plan_id, charged_value) "
                    "VALUES (:t, :a, :plan, :cv)"
                ),
                {"t": tenant_id, "a": appointment_id, "plan": plan_id, "cv": charged_value},
            )


async def _seed_five_patient_rfm_base(client, admin_engine, tenant_id, headers):
    """5 pacientes, um por segmento (ver DECISÃO na docstring do módulo):

    - "Paciente Hibernando" (m=1, menor receita): recência ruim (400d),
      1 visita só -> hibernando (tudo baixo).
    - "Paciente Novo" (m=2): recência ótima (10d), 1 visita só -> novos.
    - "Paciente Em Risco" (m=3): recência ruim (200d), 5 visitas
      (frequência >=3, mas valor no meio, não dispara "não pode
      perder") -> em_risco.
    - "Paciente Não Pode Perder" (m=4, alto valor histórico): recência
      ruim (400d) -> nao_pode_perder (valor alto pesa mais que
      frequência aqui).
    - "Paciente Campeão" (m=5, maior receita): recência ótima (5d), 10
      visitas -> campeoes.

    Receitas 100/1000/2000/3000/5000 (ordem estritamente crescente, 5
    pacientes) -> quantil limpo 1/2/3/4/5, sem empate.
    """
    plan_id = await _create_insurance_plan(admin_engine, tenant_id)
    now = datetime.now(timezone.utc)

    names_revenue = [
        ("Paciente Hibernando", 100.0),
        ("Paciente Novo", 1000.0),
        ("Paciente Em Risco", 2000.0),
        ("Paciente Não Pode Perder", 3000.0),
        ("Paciente Campeão", 5000.0),
    ]
    patient_ids = {}
    for full_name, _revenue in names_revenue:
        resp = await client.post("/api/v1/patients", json={"full_name": full_name}, headers=headers)
        patient_ids[full_name] = resp.json()["id"]

    # Hibernando: recência ruim, 1 visita, menor receita.
    await _create_appointment(
        admin_engine, tenant_id, patient_ids["Paciente Hibernando"], now - timedelta(days=400), plan_id=plan_id, charged_value=100.0
    )
    # Novo: recência ótima, 1 visita só (pouco histórico ainda).
    await _create_appointment(
        admin_engine, tenant_id, patient_ids["Paciente Novo"], now - timedelta(days=10), plan_id=plan_id, charged_value=1000.0
    )
    # Em risco: recência ruim, 5 visitas (frequência >=3).
    for i in range(5):
        await _create_appointment(
            admin_engine,
            tenant_id,
            patient_ids["Paciente Em Risco"],
            now - timedelta(days=200 + i),
            plan_id=plan_id,
            charged_value=2000.0 if i == 0 else None,
        )
    # Não pode perder: recência ruim, alto valor histórico.
    await _create_appointment(
        admin_engine, tenant_id, patient_ids["Paciente Não Pode Perder"], now - timedelta(days=400), plan_id=plan_id, charged_value=3000.0
    )
    # Campeão: recência ótima, 10 visitas, maior receita.
    for i in range(10):
        await _create_appointment(
            admin_engine,
            tenant_id,
            patient_ids["Paciente Campeão"],
            now - timedelta(days=5 + i),
            plan_id=plan_id,
            charged_value=5000.0 if i == 0 else None,
        )

    return patient_ids


async def test_rfm_classifies_five_patients_into_five_distinct_segments(client, auth_headers_a, admin_engine, tenant_a):
    await _seed_five_patient_rfm_base(client, admin_engine, tenant_a, auth_headers_a)

    response = await client.get("/api/v1/analytics/patient-rfm", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()

    assert body["total_patients"] == 5
    counts = {row["segment"]: row["patient_count"] for row in body["segment_counts"]}
    # Todos os 7 segmentos sempre aparecem, mesmo os com 0 (taxonomia fixa).
    assert set(counts) == {"campeoes", "fieis", "nao_pode_perder", "em_risco", "novos", "hibernando", "precisa_atencao"}
    assert counts["campeoes"] == 1
    assert counts["novos"] == 1
    assert counts["em_risco"] == 1
    assert counts["nao_pode_perder"] == 1
    assert counts["hibernando"] == 1
    assert counts["fieis"] == 0
    assert counts["precisa_atencao"] == 0


async def test_rfm_action_items_only_at_risk_segments_ordered_by_revenue_desc(client, auth_headers_a, admin_engine, tenant_a):
    """`action_items` é só quem precisa de ação (nao_pode_perder/em_risco),
    nunca a base inteira — maior receita histórica primeiro (mais R$ em
    risco por paciente vem antes)."""
    await _seed_five_patient_rfm_base(client, admin_engine, tenant_a, auth_headers_a)

    response = await client.get("/api/v1/analytics/patient-rfm", headers=auth_headers_a)
    body = response.json()

    action_names = [item["full_name"] for item in body["action_items"]]
    assert action_names == ["Paciente Não Pode Perder", "Paciente Em Risco"]
    assert body["action_items"][0]["segment"] == "nao_pode_perder"
    assert body["action_items"][0]["total_revenue"] == 3000.0
    assert body["action_items"][1]["segment"] == "em_risco"
    assert body["action_items"][1]["total_revenue"] == 2000.0
    # Campeão/Novo/Hibernando nunca entram na fila de ação.
    assert "Paciente Campeão" not in action_names
    assert "Paciente Novo" not in action_names
    assert "Paciente Hibernando" not in action_names


async def test_rfm_action_items_annotates_last_outreach(client, auth_headers_a, admin_engine, tenant_a):
    """Onda 4, item 12 — mesma anotação de test_inactive_patients_annotates_last_outreach
    (tests/integration/test_patient_outreach_log.py), agora na fila de
    ação do RFM."""
    patient_ids = await _seed_five_patient_rfm_base(client, admin_engine, tenant_a, auth_headers_a)

    before = await client.get("/api/v1/analytics/patient-rfm", headers=auth_headers_a)
    action_item = next(i for i in before.json()["action_items"] if i["full_name"] == "Paciente Não Pode Perder")
    assert action_item["last_outreach_at"] is None

    await client.post(
        f"/api/v1/patients/{patient_ids['Paciente Não Pode Perder']}/outreach-log",
        json={"channel": "telefone", "outcome": "agendou"},
        headers=auth_headers_a,
    )

    after = await client.get("/api/v1/analytics/patient-rfm", headers=auth_headers_a)
    action_item = next(i for i in after.json()["action_items"] if i["full_name"] == "Paciente Não Pode Perder")
    assert action_item["last_outreach_at"] is not None
    assert action_item["last_outreach_outcome"] == "agendou"


async def test_rfm_with_no_patient_history_is_all_zero(client, auth_headers_a):
    response = await client.get("/api/v1/analytics/patient-rfm", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["total_patients"] == 0
    assert body["action_items"] == []
    assert all(row["patient_count"] == 0 for row in body["segment_counts"])


async def test_rfm_isolates_between_tenants(client, auth_headers_a, auth_headers_b, admin_engine, tenant_a):
    await _seed_five_patient_rfm_base(client, admin_engine, tenant_a, auth_headers_a)

    response_b = await client.get("/api/v1/analytics/patient-rfm", headers=auth_headers_b)
    assert response_b.status_code == 200
    assert response_b.json()["total_patients"] == 0


async def test_smart_insights_flags_rfm_cannot_lose_patients(client, auth_headers_a, admin_engine, tenant_a):
    """Onda 6 do Plano de Ação, item 19 — o segmento 'não pode perder' do
    RFM (ver rfm_engine.classify_segment) alimenta um card dedicado no
    motor de insights, ponta a ponta via GET /analytics/smart-insights.
    Reaproveita a mesma base de 5 pacientes que já prova a classificação
    em app/services/rfm_engine.py — só "Paciente Não Pode Perder" deve
    aparecer, nunca "Paciente Em Risco" (segmento diferente)."""
    await _seed_five_patient_rfm_base(client, admin_engine, tenant_a, auth_headers_a)

    today = datetime.now(timezone.utc).date()
    date_to = today + timedelta(days=2)
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={today.isoformat()}&date_to={date_to.isoformat()}",
        headers=auth_headers_a,
    )
    assert response.status_code == 200
    insights = response.json()["insights"]
    cannot_lose = next((i for i in insights if "alto valor sumiu" in i["title"].lower()), None)
    assert cannot_lose is not None
    assert cannot_lose["severity"] == "warning"
    assert cannot_lose["category"] == "agenda"
    assert "Paciente Não Pode Perder" in cannot_lose["message"]
    assert "R$ 3,000.00" in cannot_lose["message"]
    assert cannot_lose["action_href"] == "#carteira-inativa"


async def test_atendimento_cannot_access_patient_rfm(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao-rfm@clinica-a.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.get("/api/v1/analytics/patient-rfm", headers=headers)
    assert response.status_code == 403
