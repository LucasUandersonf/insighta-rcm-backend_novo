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
"""
import json

from sqlalchemy import text

from app.services import whatsapp_client as wc_module


async def _set_whatsapp_destination(admin_engine, tenant_id: str, number: str = "+5511999999999") -> None:
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("UPDATE core.tenants SET whatsapp_group_id = :n WHERE id = :t"),
            {"n": number, "t": tenant_id},
        )


async def test_send_weekly_report_without_destination_number_configured(client, auth_headers_a, tenant_a):
    # tenant_a não tem whatsapp_group_id setado nesta fixture — caminho
    # de "Setup incompleto", que deve falhar de forma graciosa (200 com
    # sent_via_whatsapp=False), não um 500.
    response = await client.post("/api/v1/reports/weekly/send", json={}, headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["sent_via_whatsapp"] is False
    assert "Setup" in body["detail"]


async def test_send_weekly_report_without_whatsapp_credentials_configured(client, auth_headers_a, admin_engine, tenant_a):
    await _set_whatsapp_destination(admin_engine, tenant_a)
    # Credenciais da plataforma (WHATSAPP_ACCESS_TOKEN) não setadas no
    # ambiente de teste -> WhatsAppClient() levanta WhatsAppClientError,
    # capturado pelo endpoint, resposta graciosa.
    response = await client.post("/api/v1/reports/weekly/send", json={}, headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["sent_via_whatsapp"] is False


async def test_send_weekly_report_success_path(client, auth_headers_a, admin_engine, tenant_a, monkeypatch):
    await _set_whatsapp_destination(admin_engine, tenant_a, number="+5511988887777")

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
    assert captured["to_phone_number"] == "+5511988887777"
    assert captured["pdf_starts_with_signature"] is True


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
