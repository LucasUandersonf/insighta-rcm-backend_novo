"""
tests/integration/test_no_show_risk.py

Prova que o no_show_risk_engine (testado isoladamente em
tests/test_no_show_risk_engine.py) recebe o histórico correto quando
chamado pela pilha real: cria 3 consultas passadas com falta na mesma
combinação dia-da-semana+período, depois cria uma nova consulta na mesma
combinação e confere que ela já nasce marcada como alto risco.
"""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text


async def _insert_past_appointment(admin_engine, tenant_id, patient_id, scheduled_at, status):
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.appointments (tenant_id, patient_id, scheduled_at, status) "
                "VALUES (:t, :p, :sched, :status)"
            ),
            {"t": tenant_id, "p": patient_id, "sched": scheduled_at, "status": status},
        )


async def test_new_appointment_inherits_high_risk_from_specific_weekday_pattern(client, auth_headers_a, admin_engine, tenant_a):
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Faltoso"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    # 3 faltas passadas, todas numa segunda-feira à tarde.
    base_monday = date.today() - timedelta(days=date.today().weekday() + 7)  # uma segunda-feira no passado
    for weeks_back in range(3):
        past_monday = base_monday - timedelta(weeks=weeks_back)
        scheduled = datetime.combine(past_monday, datetime.min.time(), tzinfo=timezone.utc).replace(hour=14)
        await _insert_past_appointment(admin_engine, tenant_a, patient_id, scheduled, "no_show")

    # Nova consulta numa segunda-feira à tarde futura -> deve herdar o padrão específico.
    days_until_monday = (0 - date.today().weekday()) % 7
    next_monday = date.today() + timedelta(days=days_until_monday or 7)
    new_scheduled = datetime.combine(next_monday, datetime.min.time(), tzinfo=timezone.utc).replace(hour=14)

    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={"patient_id": patient_id, "scheduled_at": new_scheduled.isoformat()},
        headers=auth_headers_a,
    )
    assert appointment_resp.status_code == 201
    body = appointment_resp.json()
    assert body["no_show_risk_level"] == "alto"
    assert body["no_show_risk_score"] == 1.0


async def test_new_patient_without_history_is_indeterminado(client, auth_headers_a):
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Novo"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={"patient_id": patient_id, "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
        headers=auth_headers_a,
    )
    assert appointment_resp.status_code == 201
    assert appointment_resp.json()["no_show_risk_level"] == "indeterminado"
