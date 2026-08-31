"""
tests/integration/test_report_recipients.py

CRUD de destinatários de relatório via HTTP — ver DECISÃO completa em
app/sql/009_report_recipients.sql. Segue o mesmo padrão Zero Mocks dos
demais testes de integração (tests/integration/test_denial_appeals.py):
banco Postgres real, cliente HTTP real (ASGI in-process).

Nota: o briefing original pedia este arquivo em tests/api/ — este
projeto não tem esse diretório (todo teste de integração vive em
tests/integration/, ver tests/conftest.py e os demais arquivos aqui do
lado); o teste foi colocado aqui para seguir a convenção real do
repositório em vez de criar um diretório novo e paralelo.
"""


async def test_create_list_update_delete_report_recipient(client, auth_headers_a):
    create_resp = await client.post(
        "/api/v1/report-recipients",
        json={"name": "Sócio Diretor", "phone_whatsapp": "+5511999990000", "report_types": ["weekly_summary"]},
        headers=auth_headers_a,
    )
    assert create_resp.status_code == 201, create_resp.text
    body = create_resp.json()
    assert body["name"] == "Sócio Diretor"
    assert body["report_types"] == ["weekly_summary"]
    assert body["active"] is True
    recipient_id = body["id"]

    list_resp = await client.get("/api/v1/report-recipients", headers=auth_headers_a)
    assert list_resp.status_code == 200
    assert any(r["id"] == recipient_id for r in list_resp.json())

    get_resp = await client.get(f"/api/v1/report-recipients/{recipient_id}", headers=auth_headers_a)
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == recipient_id

    update_resp = await client.patch(
        f"/api/v1/report-recipients/{recipient_id}",
        json={"active": False, "email": "socio@clinica-a.com"},
        headers=auth_headers_a,
    )
    assert update_resp.status_code == 200
    updated = update_resp.json()
    assert updated["active"] is False
    assert updated["email"] == "socio@clinica-a.com"
    # Telefone original não deveria ter sido apagado por um PATCH que não o menciona.
    assert updated["phone_whatsapp"] == "+5511999990000"

    delete_resp = await client.delete(f"/api/v1/report-recipients/{recipient_id}", headers=auth_headers_a)
    assert delete_resp.status_code == 204

    get_after_delete = await client.get(f"/api/v1/report-recipients/{recipient_id}", headers=auth_headers_a)
    assert get_after_delete.status_code == 404


async def test_create_report_recipient_requires_phone_or_email(client, auth_headers_a):
    response = await client.post(
        "/api/v1/report-recipients",
        json={"name": "Contato Sem Meio de Contato"},
        headers=auth_headers_a,
    )
    assert response.status_code == 422


async def test_update_cannot_leave_recipient_without_any_contact(client, auth_headers_a):
    create_resp = await client.post(
        "/api/v1/report-recipients",
        json={"name": "Só Telefone", "phone_whatsapp": "+5511988887777"},
        headers=auth_headers_a,
    )
    recipient_id = create_resp.json()["id"]

    # PATCH tentando zerar o único contato existente (telefone), sem
    # fornecer email — deve ser rejeitado (400), não deixar o registro
    # sem nenhum contato.
    update_resp = await client.patch(
        f"/api/v1/report-recipients/{recipient_id}",
        json={"phone_whatsapp": ""},
        headers=auth_headers_a,
    )
    assert update_resp.status_code == 400


async def test_atendimento_cannot_manage_report_recipients(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao@report-recipients-test.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.post(
        "/api/v1/report-recipients",
        json={"name": "Não deveria criar", "phone_whatsapp": "+5511900000000"},
        headers=headers,
    )
    assert response.status_code == 403

    list_resp = await client.get("/api/v1/report-recipients", headers=headers)
    assert list_resp.status_code == 403


async def test_report_recipients_are_isolated_by_tenant(client, auth_headers_a, auth_headers_b):
    create_resp = await client.post(
        "/api/v1/report-recipients",
        json={"name": "Destinatário Clínica A", "phone_whatsapp": "+5511911112222"},
        headers=auth_headers_a,
    )
    recipient_id = create_resp.json()["id"]

    cross_tenant_get = await client.get(f"/api/v1/report-recipients/{recipient_id}", headers=auth_headers_b)
    assert cross_tenant_get.status_code == 404

    list_b = await client.get("/api/v1/report-recipients", headers=auth_headers_b)
    assert list_b.json() == []
