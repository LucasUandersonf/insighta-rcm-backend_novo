"""
tests/integration/test_reports.py

DECISÃO — como mockar o WhatsApp sem tocar em rede real
-------------------------------------------------------------------------
`get_settings()` é cacheada (`@lru_cache`), e `app/services/whatsapp_client.py`
lê `settings = get_settings()` UMA VEZ, no import do módulo. Setar
`os.environ["WHATSAPP_ACCESS_TOKEN"]` dentro de um teste NÃO teria efeito
nenhum a essa altura — o objeto Settings já foi criado e cacheado antes.
A saída é usar `monkeypatch.setattr` diretamente no OBJETO já
instanciado (`wc_module.settings`), mutando o atributo no lugar, em vez
de tentar forçar uma nova leitura de variável de ambiente. E para não
fazer uma chamada HTTP real à Meta, também trocamos o método
`send_weekly_report` da classe por uma função falsa via monkeypatch —
técnica padrão de teste, não um hack específico deste projeto.

Destinatários agora vêm de core.report_recipients (ver
app/sql/009_report_recipients.sql), não mais de `Tenant.whatsapp_group_id`
— ver DECISÃO completa em app/services/report_send_service.py sobre o
bug corrigido nesta mesma sessão (o endpoint ainda lia o campo velho).
"""
import uuid

from sqlalchemy import text

from app.services import report_send_service as rss_module
from app.services import whatsapp_client as wc_module


async def _insert_report_recipient(admin_engine, tenant_id: str, *, phone_whatsapp: str, name: str = "Sócio") -> None:
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.report_recipients (id, tenant_id, name, phone_whatsapp) VALUES (:id, :t, :n, :p)"),
            {"id": str(uuid.uuid4()), "t": tenant_id, "n": name, "p": phone_whatsapp},
        )


async def test_send_weekly_report_without_recipients_configured(client, auth_headers_a, tenant_a):
    # tenant_a não tem nenhum core.report_recipients cadastrado nesta
    # fixture — caminho de "Setup incompleto", que deve falhar de forma
    # graciosa (200 com sent_via_whatsapp=False), não um 500 nem um 503
    # (falta de destinatário é diferente de falta de credencial da
    # plataforma — ver DECISÃO em report_send_service.py).
    response = await client.post("/api/v1/reports/weekly/send", json={}, headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["sent_via_whatsapp"] is False
    assert body["recipients_checked"] == 0
    assert "destinatário" in body["detail"].lower()


async def test_send_weekly_report_without_whatsapp_credentials_configured(client, auth_headers_a, admin_engine, tenant_a):
    await _insert_report_recipient(admin_engine, tenant_a, phone_whatsapp="+5511999999999")
    # Existe destinatário, mas as credenciais da PLATAFORMA
    # (WHATSAPP_ACCESS_TOKEN) não estão setadas no ambiente de teste ->
    # WhatsAppClient() levanta WhatsAppClientError -> 503 (problema de
    # configuração de infra, não de cadastro do tenant).
    response = await client.post("/api/v1/reports/weekly/send", json={}, headers=auth_headers_a)
    assert response.status_code == 503


async def test_send_weekly_report_success_path(client, auth_headers_a, admin_engine, tenant_a, monkeypatch):
    await _insert_report_recipient(admin_engine, tenant_a, phone_whatsapp="+5511988887777")

    monkeypatch.setattr(wc_module.settings, "WHATSAPP_ACCESS_TOKEN", "token-falso-de-teste")
    monkeypatch.setattr(wc_module.settings, "WHATSAPP_PHONE_NUMBER_ID", "id-falso-de-teste")

    captured = {}

    async def fake_send_weekly_report(self, *, to_phone_number, pdf_bytes, filename):
        captured["to_phone_number"] = to_phone_number
        captured["pdf_starts_with_signature"] = pdf_bytes[:4] == b"%PDF"
        captured["filename"] = filename
        return "wamid.fake-id-123"

    monkeypatch.setattr(wc_module.WhatsAppClient, "send_weekly_report", fake_send_weekly_report)

    response = await client.post("/api/v1/reports/weekly/send", json={}, headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["sent_via_whatsapp"] is True
    assert body["recipients_checked"] == 1
    assert body["sent"] == 1
    assert body["failed"] == 0
    assert captured["to_phone_number"] == "+5511988887777"
    assert captured["pdf_starts_with_signature"] is True


async def test_send_weekly_report_to_multiple_recipients(client, auth_headers_a, admin_engine, tenant_a, monkeypatch):
    """core.report_recipients suporta N destinatários por tenant (ver
    DECISÃO em 009_report_recipients.sql) — prova que o fan-out de
    verdade manda para todos, não só o primeiro."""
    await _insert_report_recipient(admin_engine, tenant_a, phone_whatsapp="+5511911111111", name="Sócio 1")
    await _insert_report_recipient(admin_engine, tenant_a, phone_whatsapp="+5511922222222", name="Sócio 2")

    monkeypatch.setattr(wc_module.settings, "WHATSAPP_ACCESS_TOKEN", "token-falso-de-teste")
    monkeypatch.setattr(wc_module.settings, "WHATSAPP_PHONE_NUMBER_ID", "id-falso-de-teste")

    sent_to = []

    async def fake_send_weekly_report(self, *, to_phone_number, pdf_bytes, filename):
        sent_to.append(to_phone_number)
        return "wamid.fake-id-123"

    monkeypatch.setattr(wc_module.WhatsAppClient, "send_weekly_report", fake_send_weekly_report)

    response = await client.post("/api/v1/reports/weekly/send", json={}, headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["recipients_checked"] == 2
    assert body["sent"] == 2
    assert set(sent_to) == {"+5511911111111", "+5511922222222"}


async def test_send_weekly_report_total_failure_triggers_monitoring_alert(
    client, auth_headers_a, admin_engine, tenant_a, monkeypatch
):
    """Rodada de monitoramento/alertas: falha em 100% dos destinatários
    (ex: token da Meta expirado) precisa virar um alerta ATIVO — não só
    mais uma linha `INFO` no log que ninguém monitora. Ver DECISÃO em
    report_send_service._alert_if_total_send_failure."""
    await _insert_report_recipient(admin_engine, tenant_a, phone_whatsapp="+5511988887777")

    monkeypatch.setattr(wc_module.settings, "WHATSAPP_ACCESS_TOKEN", "token-falso-de-teste")
    monkeypatch.setattr(wc_module.settings, "WHATSAPP_PHONE_NUMBER_ID", "id-falso-de-teste")

    async def failing_send(self, *, to_phone_number, pdf_bytes, filename):
        raise wc_module.WhatsAppClientError("token expirado")

    monkeypatch.setattr(wc_module.WhatsAppClient, "send_weekly_report", failing_send)

    # `report_send_service.settings` é o MESMO objeto cacheado que
    # `wc_module.settings` (ambos vêm de get_settings(), com @lru_cache)
    # — setamos SENTRY_DSN aqui só para exercitar o branch que chama
    # sentry_sdk.capture_message, sem depender de uma conta real.
    monkeypatch.setattr(rss_module.settings, "SENTRY_DSN", "https://fake@sentry.example/1")
    captured = {}

    def fake_capture_message(message, level=None):
        captured["message"] = message
        captured["level"] = level

    monkeypatch.setattr(rss_module.sentry_sdk, "capture_message", fake_capture_message)

    response = await client.post("/api/v1/reports/weekly/send", json={}, headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["sent"] == 0
    assert body["failed"] == 1
    assert captured["level"] == "error"
    assert "relatório semanal" in captured["message"]
    assert str(tenant_a) in captured["message"]


async def test_send_weekly_report_requires_admin_or_owner_role(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="atendimento@rbac-report.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])

    response = await client.post(
        "/api/v1/reports/weekly/send", json={}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403


async def test_send_weekly_report_rejects_invalid_period(client, auth_headers_a):
    response = await client.post(
        "/api/v1/reports/weekly/send",
        json={"period_start": "2026-08-20", "period_end": "2026-08-10"},  # fim antes do início
        headers=auth_headers_a,
    )
    assert response.status_code == 400


async def test_send_weekly_report_defaults_to_current_week_to_date(client, auth_headers_a):
    """Achado do usuário sobre lacuna de produto: o disparo sob demanda
    deve refletir o retrato mais fresco possível (semana em andamento
    até hoje), não a última semana FECHADA (essa é a lógica do cron
    semanal, deliberadamente diferente — ver DECISÃO em reports.py)."""
    from datetime import date, timedelta

    response = await client.post("/api/v1/reports/weekly/send", json={}, headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    today = date.today()
    expected_monday = today - timedelta(days=today.weekday())
    assert body["period_start"] == expected_monday.isoformat()
    assert body["period_end"] == today.isoformat()
