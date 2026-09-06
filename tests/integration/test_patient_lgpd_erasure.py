"""
tests/integration/test_patient_lgpd_erasure.py

Direito de eliminação do titular (LGPD art. 18, VI) — ver DECISÃO
completa em app/services/patient_service.py (anonymize_patient) e
app/sql/022_patient_lgpd_erasure.sql. Cobertura de "escrita de audit_log
sem PII" fica em test_audit_log.py; aqui é o comportamento do próprio
endpoint (RBAC, idempotência, o que de fato é apagado/preservado).
"""


async def _create_patient(client, auth_headers, *, full_name="Paciente LGPD", cpf="98765432100") -> str:
    resp = await client.post("/api/v1/patients", json={"full_name": full_name, "cpf": cpf}, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_anonymize_patient_scrubs_pii_but_keeps_id(client, auth_headers_a):
    patient_id = await _create_patient(client, auth_headers_a, full_name="Fulano de Tal", cpf="11122233344")

    resp = await client.post(f"/api/v1/patients/{patient_id}/anonymize", headers=auth_headers_a)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == patient_id
    assert body["full_name"] != "Fulano de Tal"
    assert body["cpf"] is None
    assert body["birth_date"] is None
    assert body["anonymized_at"] is not None


async def test_anonymize_patient_is_reflected_in_list(client, auth_headers_a):
    """Depois de anonimizado, o paciente continua aparecendo na listagem
    (o histórico de agendamento/faturamento vinculado a ele precisa
    continuar navegável) — só o nome exibido é que muda."""
    patient_id = await _create_patient(client, auth_headers_a, full_name="Nome Original")
    await client.post(f"/api/v1/patients/{patient_id}/anonymize", headers=auth_headers_a)

    list_resp = await client.get("/api/v1/patients", headers=auth_headers_a)
    assert list_resp.status_code == 200
    items = {i["id"]: i for i in list_resp.json()["items"]}
    assert patient_id in items
    assert items[patient_id]["full_name"] != "Nome Original"


async def test_anonymize_patient_twice_is_rejected(client, auth_headers_a):
    patient_id = await _create_patient(client, auth_headers_a)
    first = await client.post(f"/api/v1/patients/{patient_id}/anonymize", headers=auth_headers_a)
    assert first.status_code == 200

    second = await client.post(f"/api/v1/patients/{patient_id}/anonymize", headers=auth_headers_a)
    assert second.status_code == 409


async def test_anonymize_unknown_patient_404(client, auth_headers_a):
    resp = await client.post(
        "/api/v1/patients/00000000-0000-0000-0000-000000000000/anonymize", headers=auth_headers_a
    )
    assert resp.status_code == 404


async def test_atendimento_cannot_anonymize_patient(client, admin_engine, tenant_a, auth_headers_a):
    """Diferente de criar/ver paciente (rotina de recepção), anonimizar é
    uma decisão de conformidade irreversível — fora do alcance de
    'atendimento', mesmo essa role podendo cadastrar o paciente."""
    from tests.conftest import _insert_user, _login

    patient_id = await _create_patient(client, auth_headers_a)

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao@lgpd-test.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])

    resp = await client.post(
        f"/api/v1/patients/{patient_id}/anonymize", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403


async def test_admin_can_anonymize_patient(client, admin_engine, tenant_a, auth_headers_a):
    from tests.conftest import _insert_user, _login

    patient_id = await _create_patient(client, auth_headers_a)

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="admin@lgpd-test.com", role="admin")
    token = await _login(client, user["email"], user["password"])

    resp = await client.post(
        f"/api/v1/patients/{patient_id}/anonymize", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
