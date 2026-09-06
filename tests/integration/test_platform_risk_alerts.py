"""
tests/integration/test_platform_risk_alerts.py — alertas proativos de
Customer Success (ver DECISÃO completa em app/services/platform_alert_service.py
e app/sql/027_platform_risk_alerts.sql).

Mesma técnica de test_platform_customer_success.py (monkeypatch de
platform_module.settings para a senha) + mesma técnica de
test_support_requests.py (monkeypatch de EmailClient.send) para provar
o e-mail sem depender de SMTP real.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.api.v1.endpoints import platform as platform_module
from app.services import platform_alert_service as alert_service_module

_PASSWORD = "senha-super-secreta-da-equipe"
_ALERT_EMAIL = "cs@insighta-rcm.com"


@pytest.fixture(autouse=True)
def _configure_platform(monkeypatch):
    monkeypatch.setattr(platform_module.settings, "PLATFORM_ADMIN_PASSWORD", _PASSWORD)
    monkeypatch.setattr(alert_service_module.settings, "PLATFORM_ALERT_EMAIL", _ALERT_EMAIL)
    yield


@pytest.fixture
def _sent_emails(monkeypatch):
    sent = []

    async def fake_send(self, *, to_email, subject, text_body, html_body=None):
        sent.append({"to_email": to_email, "subject": subject, "text_body": text_body})

    monkeypatch.setattr(alert_service_module.EmailClient, "send", fake_send)
    return sent


async def _platform_login(client) -> str:
    resp = await client.post("/api/v1/platform/login", json={"password": _PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _run_alerts(client) -> dict:
    token = await _platform_login(client)
    resp = await client.post("/api/v1/platform/alerts/run", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _set_tenant_created_at(admin_engine, tenant_id: str, when: datetime) -> None:
    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE core.tenants SET created_at = :when WHERE id = :id"), {"when": when, "id": tenant_id})


async def _insert_audit_events(admin_engine, tenant_id: str, count: int) -> None:
    async with admin_engine.begin() as conn:
        for _ in range(count):
            await conn.execute(
                text(
                    "INSERT INTO core.audit_log (tenant_id, action, entity_type, entity_id) "
                    "VALUES (:tenant_id, 'created', 'patient', :entity_id)"
                ),
                {"tenant_id": tenant_id, "entity_id": str(uuid.uuid4())},
            )


async def _backdate_last_alert(admin_engine, tenant_id: str, when: datetime) -> None:
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("UPDATE core.platform_risk_alerts SET last_alert_sent_at = :when WHERE tenant_id = :id"),
            {"when": when, "id": tenant_id},
        )


async def _make_tenant_at_risk(admin_engine, tenant_id: str) -> None:
    await _set_tenant_created_at(admin_engine, tenant_id, datetime.now(timezone.utc) - timedelta(days=30))


async def test_alerts_run_requires_platform_token(client):
    resp = await client.post("/api/v1/platform/alerts/run")
    assert resp.status_code == 401


async def test_new_risk_tenant_triggers_email_and_opens_episode(client, admin_engine, tenant_a, _sent_emails):
    await _make_tenant_at_risk(admin_engine, tenant_a)

    result = await _run_alerts(client)
    assert result["new_alerts"] == ["Clínica A"]
    assert len(_sent_emails) == 1
    assert _sent_emails[0]["to_email"] == _ALERT_EMAIL
    assert "entrou em risco" in _sent_emails[0]["subject"]

    async with admin_engine.begin() as conn:
        row = (await conn.execute(text("SELECT tenant_id FROM core.platform_risk_alerts WHERE tenant_id = :id"), {"id": tenant_a})).first()
    assert row is not None


async def test_second_run_within_interval_does_not_resend(client, admin_engine, tenant_a, _sent_emails):
    await _make_tenant_at_risk(admin_engine, tenant_a)

    first = await _run_alerts(client)
    assert len(first["new_alerts"]) == 1

    second = await _run_alerts(client)
    assert second["new_alerts"] == []
    assert second["reminders_sent"] == []
    # Só o e-mail da primeira rodada — nada reenviado na segunda.
    assert len(_sent_emails) == 1


async def test_reminder_sent_after_interval(client, admin_engine, tenant_a, _sent_emails):
    await _make_tenant_at_risk(admin_engine, tenant_a)
    await _run_alerts(client)
    assert len(_sent_emails) == 1

    await _backdate_last_alert(admin_engine, tenant_a, datetime.now(timezone.utc) - timedelta(days=8))

    result = await _run_alerts(client)
    assert result["new_alerts"] == []
    assert len(result["reminders_sent"]) == 1
    assert len(_sent_emails) == 2
    assert "segue em risco" in _sent_emails[1]["subject"]


async def test_recovered_tenant_closes_episode_without_new_email(client, admin_engine, tenant_a, _sent_emails):
    await _make_tenant_at_risk(admin_engine, tenant_a)
    await _run_alerts(client)
    assert len(_sent_emails) == 1

    # Clínica volta a ter atividade — sai do status "risco".
    await _insert_audit_events(admin_engine, tenant_a, count=6)

    result = await _run_alerts(client)
    assert len(result["recovered"]) == 1
    assert result["new_alerts"] == []
    assert result["reminders_sent"] == []
    # Nenhum e-mail novo — só o da primeira rodada continua na lista.
    assert len(_sent_emails) == 1

    async with admin_engine.begin() as conn:
        row = (await conn.execute(text("SELECT tenant_id FROM core.platform_risk_alerts WHERE tenant_id = :id"), {"id": tenant_a})).first()
    assert row is None


async def test_re_entering_risk_after_recovery_counts_as_new_alert(client, admin_engine, tenant_a, _sent_emails):
    await _make_tenant_at_risk(admin_engine, tenant_a)
    await _run_alerts(client)
    await _insert_audit_events(admin_engine, tenant_a, count=6)
    await _run_alerts(client)  # fecha o episódio
    assert len(_sent_emails) == 1

    # Simula "30 dias sem novo evento" apagando a atividade recente (mais
    # simples e determinístico neste teste HTTP-completo do que avançar o
    # relógio real) — volta a cair em events_last_30d == 0 -> "risco" de novo.
    async with admin_engine.begin() as conn:
        await conn.execute(text("DELETE FROM core.audit_log WHERE tenant_id = :id"), {"id": tenant_a})

    result = await _run_alerts(client)
    assert result["new_alerts"] == ["Clínica A"]
    assert len(_sent_emails) == 2


async def test_no_alert_email_configured_still_records_episode(client, admin_engine, tenant_a, monkeypatch, _sent_emails):
    monkeypatch.setattr(alert_service_module.settings, "PLATFORM_ALERT_EMAIL", None)
    await _make_tenant_at_risk(admin_engine, tenant_a)

    result = await _run_alerts(client)
    assert len(result["new_alerts"]) == 1
    assert _sent_emails == []

    async with admin_engine.begin() as conn:
        row = (await conn.execute(text("SELECT tenant_id FROM core.platform_risk_alerts WHERE tenant_id = :id"), {"id": tenant_a})).first()
    assert row is not None


async def test_email_failure_does_not_break_the_run(client, admin_engine, tenant_a, monkeypatch):
    async def failing_send(self, **kwargs):
        raise RuntimeError("SMTP fora do ar (simulado)")

    monkeypatch.setattr(alert_service_module.EmailClient, "send", failing_send)
    await _make_tenant_at_risk(admin_engine, tenant_a)

    result = await _run_alerts(client)
    assert len(result["new_alerts"]) == 1  # episódio registrado mesmo com falha de e-mail
