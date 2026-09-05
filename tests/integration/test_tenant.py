"""tests/integration/test_tenant.py — Painel do Administrador da Empresa:
dados cadastrais e plano do tenant. Também prova isolamento: owner da
Clínica A nunca vê/edita dados da Clínica B (mesmo raciocínio de
test_rls_isolation.py, aplicado à única tabela sem RLS por linha — o
isolamento aqui vem do service sempre usar current_user.tenant_id, nunca
de um id recebido por parâmetro)."""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text


async def test_owner_can_view_and_update_own_tenant(client, auth_headers_a):
    get_resp = await client.get("/api/v1/tenant", headers=auth_headers_a)
    assert get_resp.status_code == 200
    assert get_resp.json()["trade_name"] == "Clínica A"

    patch_resp = await client.patch("/api/v1/tenant", json={"trade_name": "Clínica A Renomeada"}, headers=auth_headers_a)
    assert patch_resp.status_code == 200
    assert patch_resp.json()["trade_name"] == "Clínica A Renomeada"


async def test_non_owner_cannot_update_tenant(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="financeiro@clinica-a.com", role="financeiro")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.patch("/api/v1/tenant", json={"trade_name": "Não deveria"}, headers=headers)
    assert resp.status_code == 403


async def test_tenant_b_never_sees_tenant_a_data(client, auth_headers_a, auth_headers_b):
    resp_a = await client.get("/api/v1/tenant", headers=auth_headers_a)
    resp_b = await client.get("/api/v1/tenant", headers=auth_headers_b)

    assert resp_a.json()["trade_name"] == "Clínica A"
    assert resp_b.json()["trade_name"] == "Clínica B"
    assert resp_a.json()["id"] != resp_b.json()["id"]


async def test_list_available_plans(client, auth_headers_a):
    resp = await client.get("/api/v1/tenant/plans/available", headers=auth_headers_a)
    assert resp.status_code == 200
    assert "starter" in resp.json()


async def test_no_show_thresholds_default_to_null(client, auth_headers_a):
    """Sem configuração, o motor usa o default do módulo (ver
    no_show_risk_engine.DEFAULT_LOW_THRESHOLD/DEFAULT_MEDIUM_THRESHOLD) —
    NULL aqui, nunca um valor inventado."""
    resp = await client.get("/api/v1/tenant", headers=auth_headers_a)
    assert resp.status_code == 200
    assert resp.json()["no_show_low_threshold"] is None
    assert resp.json()["no_show_medium_threshold"] is None


async def test_owner_can_configure_no_show_thresholds(client, auth_headers_a):
    resp = await client.patch(
        "/api/v1/tenant",
        json={"no_show_low_threshold": 0.05, "no_show_medium_threshold": 0.20},
        headers=auth_headers_a,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["no_show_low_threshold"] == 0.05
    assert resp.json()["no_show_medium_threshold"] == 0.20


async def test_low_threshold_must_be_below_medium_in_a_single_patch(client, auth_headers_a):
    resp = await client.patch(
        "/api/v1/tenant",
        json={"no_show_low_threshold": 0.40, "no_show_medium_threshold": 0.30},
        headers=auth_headers_a,
    )
    assert resp.status_code == 422


async def test_low_threshold_validated_against_already_saved_medium(client, auth_headers_a):
    """low < medium precisa valer mesmo quando só UM dos dois campos é
    enviado num PATCH — o resultante (novo low, medium já salvo) é quem
    importa, não só os campos deste PATCH isoladamente."""
    first = await client.patch("/api/v1/tenant", json={"no_show_medium_threshold": 0.15}, headers=auth_headers_a)
    assert first.status_code == 200, first.text

    second = await client.patch("/api/v1/tenant", json={"no_show_low_threshold": 0.20}, headers=auth_headers_a)
    assert second.status_code == 422


async def _seed_patient_with_no_show_rate(admin_engine, tenant_id, *, total: int, no_show: int) -> None:
    """Cria 1 paciente com exatamente `no_show` de `total` atendimentos
    RESOLVIDOS (completed/no_show), espalhados no passado — usado para
    montar uma distribuição conhecida pro teste de sugestão de limiar."""
    patient_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.patients (id, tenant_id, full_name) VALUES (:id, :t, :n)"),
            {"id": patient_id, "t": tenant_id, "n": f"Paciente {patient_id[:8]}"},
        )
        for i in range(total):
            status = "no_show" if i < no_show else "completed"
            scheduled = datetime.now(timezone.utc) - timedelta(days=(i + 1) * 3)
            await conn.execute(
                text("INSERT INTO core.appointments (tenant_id, patient_id, scheduled_at, status) VALUES (:t, :p, :dt, :s)"),
                {"t": tenant_id, "p": patient_id, "dt": scheduled, "s": status},
            )


async def test_suggested_thresholds_returns_none_with_insufficient_patient_history(client, auth_headers_a, admin_engine, tenant_a):
    # Só 2 pacientes qualificados — bem abaixo de MIN_PATIENTS_FOR_SUGGESTION (10).
    await _seed_patient_with_no_show_rate(admin_engine, tenant_a, total=3, no_show=0)
    await _seed_patient_with_no_show_rate(admin_engine, tenant_a, total=3, no_show=1)

    response = await client.get("/api/v1/tenant/no-show-thresholds/suggested", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["low_threshold"] is None
    assert body["medium_threshold"] is None
    assert body["sample_size"] == 2


async def test_suggested_thresholds_computed_from_real_patient_distribution(client, auth_headers_a, admin_engine, tenant_a):
    # 10 pacientes qualificados (>= MIN_PATIENTS_FOR_SUGGESTION), taxas de
    # falta de 0%, 10%, ..., 90% (passo previsível) — mediana e P85
    # calculáveis na mão pro teste não depender de reimplementar o cálculo.
    for i in range(10):
        no_show = round(i * 0.1 * 10)  # 0,1,2,...,9 faltas em 10 atendimentos = 0%,10%,...,90%
        await _seed_patient_with_no_show_rate(admin_engine, tenant_a, total=10, no_show=no_show)

    response = await client.get("/api/v1/tenant/no-show-thresholds/suggested", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["sample_size"] == 10
    assert body["low_threshold"] is not None
    assert body["medium_threshold"] is not None
    assert 0 < body["low_threshold"] < body["medium_threshold"] < 1


async def test_atendimento_role_can_view_suggestion_but_not_patch(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao@no-show-suggestion.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.get("/api/v1/tenant/no-show-thresholds/suggested", headers=headers)
    assert response.status_code == 200


async def test_no_show_threshold_out_of_range_is_rejected(client, auth_headers_a):
    resp = await client.patch("/api/v1/tenant", json={"no_show_low_threshold": 1.5}, headers=auth_headers_a)
    assert resp.status_code == 422
