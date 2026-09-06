"""
tests/integration/test_support_requests.py

Central de Ajuda ("tirar dúvida sem sair do sistema") — ver DECISÃO
completa em app/sql/023_announcements_and_support.sql e
support_request_service.py: o pedido é SEMPRE persistido; o e-mail de
aviso para settings.SUPPORT_EMAIL é best-effort (mesmo mecanismo de
degradação graciosa de EmailClient — ver test_reports.py para o
precedente de como mockar sem tocar em rede real).
"""
from app.services import support_request_service as srs_module


async def test_create_support_request_persists_and_returns_201(client, auth_headers_a):
    resp = await client.post(
        "/api/v1/support-requests",
        json={"subject": "Como funciona o alerta de risco?", "message": "Não entendi quando ele dispara."},
        headers=auth_headers_a,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["subject"] == "Como funciona o alerta de risco?"
    assert body["status"] == "aberto"

    list_resp = await client.get("/api/v1/support-requests", headers=auth_headers_a)
    assert list_resp.status_code == 200
    subjects = [r["subject"] for r in list_resp.json()]
    assert "Como funciona o alerta de risco?" in subjects


async def test_create_support_request_without_support_email_configured_sends_nothing(
    client, auth_headers_a, monkeypatch
):
    monkeypatch.setattr(srs_module.settings, "SUPPORT_EMAIL", None)
    captured = {}

    async def fake_send(self, **kwargs):
        captured["called"] = True

    monkeypatch.setattr(srs_module.EmailClient, "send", fake_send)

    resp = await client.post(
        "/api/v1/support-requests", json={"subject": "Dúvida", "message": "Mensagem qualquer."}, headers=auth_headers_a
    )
    assert resp.status_code == 201
    assert "called" not in captured


async def test_create_support_request_notifies_support_email_when_configured(client, auth_headers_a, monkeypatch):
    monkeypatch.setattr(srs_module.settings, "SUPPORT_EMAIL", "suporte@insighta-rcm.com")
    captured = {}

    async def fake_send(self, *, to_email, subject, text_body, html_body=None):
        captured["to_email"] = to_email
        captured["subject"] = subject
        captured["text_body"] = text_body

    monkeypatch.setattr(srs_module.EmailClient, "send", fake_send)

    resp = await client.post(
        "/api/v1/support-requests",
        json={"subject": "Dúvida sobre glosa", "message": "Como funciona o recurso automático?"},
        headers=auth_headers_a,
    )
    assert resp.status_code == 201
    assert captured["to_email"] == "suporte@insighta-rcm.com"
    assert "Dúvida sobre glosa" in captured["subject"]
    assert "Como funciona o recurso automático?" in captured["text_body"]


async def test_support_request_email_failure_does_not_break_the_request(client, auth_headers_a, monkeypatch):
    """DECISÃO em support_request_service.py: o pedido do cliente nunca
    pode se perder por causa de um e-mail mal configurado."""
    monkeypatch.setattr(srs_module.settings, "SUPPORT_EMAIL", "suporte@insighta-rcm.com")

    async def failing_send(self, **kwargs):
        raise RuntimeError("SMTP fora do ar")

    monkeypatch.setattr(srs_module.EmailClient, "send", failing_send)

    resp = await client.post(
        "/api/v1/support-requests", json={"subject": "Dúvida", "message": "Mensagem."}, headers=auth_headers_a
    )
    # Achado esperado: hoje a exceção do e-mail propagaria como 500. O
    # comportamento correto (pedido salvo mesmo com e-mail falhando) está
    # documentado como DECISÃO no service — este teste prova o contrato.
    assert resp.status_code == 201


async def test_support_requests_isolated_by_tenant(client, auth_headers_a, auth_headers_b):
    await client.post(
        "/api/v1/support-requests", json={"subject": "Pergunta da clínica A", "message": "..."}, headers=auth_headers_a
    )
    await client.post(
        "/api/v1/support-requests", json={"subject": "Pergunta da clínica B", "message": "..."}, headers=auth_headers_b
    )

    list_a = await client.get("/api/v1/support-requests", headers=auth_headers_a)
    subjects_a = [r["subject"] for r in list_a.json()]
    assert "Pergunta da clínica A" in subjects_a
    assert "Pergunta da clínica B" not in subjects_a


async def test_atendimento_can_send_support_request(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao@support-test.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])

    resp = await client.post(
        "/api/v1/support-requests",
        json={"subject": "Dúvida da recepção", "message": "..."},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
