"""
tests/integration/test_daily_risk_alert.py

Ponta a ponta via HTTP para POST /reports/risk-alert/send — irmão de
test_reports.py (mesmo mecanismo de mock do WhatsApp), mas cobrindo o
alerta diário de risco de falta (app/services/report_send_service.py
send_daily_risk_alert) em vez do relatório semanal.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.services import whatsapp_client as wc_module
from tests.integration.test_no_show_risk import _insert_past_appointment


async def _insert_report_recipient(admin_engine, tenant_id: str, *, phone_whatsapp: str, name: str = "Recepção") -> None:
    # `report_types` fica de fora do INSERT de propósito: o default da
    # coluna ('{}', ver app/models/report_recipient.py) é o curinga
    # "recebe todos os tipos de relatório", exatamente o que os testes
    # deste arquivo precisam — mesmo padrão de test_reports.py.
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.report_recipients (id, tenant_id, name, phone_whatsapp) VALUES (:id, :t, :n, :p)"),
            {"id": str(uuid.uuid4()), "t": tenant_id, "n": name, "p": phone_whatsapp},
        )


async def _seed_high_risk_future_appointment(client, admin_engine, tenant_id, auth_headers, *, hours_ahead: int) -> str:
    """Sobe 3 faltas passadas em combinações dia/período DIFERENTES (sem
    padrão específico repetido) para o mesmo paciente — garante uma taxa
    GERAL de falta de 100%, sem depender do padrão específico de
    weekday+período (que exigiria controlar o dia da semana exato do
    agendamento futuro). Com os limiares padrão (10%/30%), 100% de falta
    cai em risco 'alto' independente do dia."""
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Alto Risco"}, headers=auth_headers)
    patient_id = patient_resp.json()["id"]

    base = datetime.now(timezone.utc) - timedelta(days=60)
    for i in range(3):
        scheduled = base + timedelta(days=i * 10, hours=i * 5)
        await _insert_past_appointment(admin_engine, tenant_id, patient_id, scheduled, "no_show")

    future_scheduled = datetime.now(timezone.utc) + timedelta(hours=hours_ahead)
    appt_resp = await client.post(
        "/api/v1/appointments",
        json={"patient_id": patient_id, "scheduled_at": future_scheduled.isoformat()},
        headers=auth_headers,
    )
    assert appt_resp.status_code == 201, appt_resp.text
    assert appt_resp.json()["no_show_risk_level"] == "alto"
    return appt_resp.json()["id"]


async def test_risk_alert_without_recipients_configured(client, auth_headers_a):
    response = await client.post("/api/v1/reports/risk-alert/send", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["sent_via_whatsapp"] is False
    assert body["recipients_checked"] == 0
    assert "destinatário" in body["detail"].lower()


async def test_risk_alert_without_high_risk_appointments_is_a_happy_path(client, auth_headers_a, admin_engine, tenant_a):
    """Destinatário existe, mas não há nenhum agendamento de risco alto
    nas próximas 24h — caminho feliz, não erro (ver DECISÃO em
    send_daily_risk_alert)."""
    await _insert_report_recipient(admin_engine, tenant_a, phone_whatsapp="+5511999999999")

    response = await client.post("/api/v1/reports/risk-alert/send", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["sent_via_whatsapp"] is False
    assert body["recipients_checked"] == 1
    assert body["high_risk_appointments"] == 0
    assert "nada a alertar" in body["detail"].lower()


async def test_risk_alert_without_whatsapp_credentials_configured(client, auth_headers_a, admin_engine, tenant_a):
    await _insert_report_recipient(admin_engine, tenant_a, phone_whatsapp="+5511999999999")
    await _seed_high_risk_future_appointment(client, admin_engine, tenant_a, auth_headers_a, hours_ahead=3)

    # Sem WHATSAPP_ACCESS_TOKEN/WHATSAPP_PHONE_NUMBER_ID no ambiente de
    # teste -> WhatsAppClient() levanta WhatsAppClientError -> 503.
    response = await client.post("/api/v1/reports/risk-alert/send", headers=auth_headers_a)
    assert response.status_code == 503


async def test_risk_alert_success_path_sends_pdf_for_upcoming_high_risk_appointment(
    client, auth_headers_a, admin_engine, tenant_a, monkeypatch
):
    await _insert_report_recipient(admin_engine, tenant_a, phone_whatsapp="+5511988887777")
    await _seed_high_risk_future_appointment(client, admin_engine, tenant_a, auth_headers_a, hours_ahead=3)

    monkeypatch.setattr(wc_module.settings, "WHATSAPP_ACCESS_TOKEN", "token-falso-de-teste")
    monkeypatch.setattr(wc_module.settings, "WHATSAPP_PHONE_NUMBER_ID", "id-falso-de-teste")

    captured = {}

    async def fake_send_weekly_report(self, *, to_phone_number, pdf_bytes, filename):
        captured["to_phone_number"] = to_phone_number
        captured["pdf_starts_with_signature"] = pdf_bytes[:4] == b"%PDF"
        captured["filename"] = filename
        return "wamid.fake-id-456"

    monkeypatch.setattr(wc_module.WhatsAppClient, "send_weekly_report", fake_send_weekly_report)

    response = await client.post("/api/v1/reports/risk-alert/send", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["sent_via_whatsapp"] is True
    assert body["recipients_checked"] == 1
    assert body["high_risk_appointments"] == 1
    assert body["sent"] == 1
    assert body["failed"] == 0
    assert captured["to_phone_number"] == "+5511988887777"
    assert captured["pdf_starts_with_signature"] is True


async def test_risk_alert_ignores_high_risk_appointment_beyond_24h_window(
    client, auth_headers_a, admin_engine, tenant_a, monkeypatch
):
    """Um agendamento de risco alto daqui a 3 dias não deveria disparar
    o alerta de "próximas 24h" — prova o parâmetro `until` de
    AnalyticsRepository.upcoming_risk_appointments."""
    await _insert_report_recipient(admin_engine, tenant_a, phone_whatsapp="+5511999999999")
    await _seed_high_risk_future_appointment(client, admin_engine, tenant_a, auth_headers_a, hours_ahead=72)

    response = await client.post("/api/v1/reports/risk-alert/send", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["high_risk_appointments"] == 0
    assert body["sent_via_whatsapp"] is False


async def test_risk_alert_requires_admin_or_owner_role(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="atendimento@rbac-risk-alert.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])

    response = await client.post("/api/v1/reports/risk-alert/send", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
