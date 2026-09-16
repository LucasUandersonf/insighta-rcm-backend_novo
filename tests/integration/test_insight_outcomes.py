"""
tests/integration/test_insight_outcomes.py

Plano Diretor Insighta — épicos F1.2 (ciclo fechado de insight) + F1.3
(atribuição/workflow). Ver DECISÃO completa em
app/sql/038_insight_outcomes.sql.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.worker import insight_outcome_reevaluation_job
from tests.conftest import _insert_user, _login


async def _create_outcome(client, auth_headers, *, title="Maior perda concentrada no convênio Unimed", assigned_to=None, due_date=None) -> dict:
    payload = {
        "source": "raiox",
        "category": "faturamento",
        "severity": "critical",
        "title": title,
        "message": "R$ 200,00 de perda no período.",
        "financial_impact": 200.0,
    }
    if assigned_to is not None:
        payload["assigned_to"] = assigned_to
    if due_date is not None:
        payload["due_date"] = due_date
    response = await client.post("/api/v1/insight-outcomes", json=payload, headers=auth_headers)
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_outcome_derives_stable_insight_key(client, auth_headers_a):
    outcome = await _create_outcome(client, auth_headers_a, title="Maior perda concentrada no convênio Unimed Nacional")
    assert outcome["status"] == "pendente"
    assert outcome["insight_key"] == "faturamento_maior_perda_concentrada_no_convenio_unimed_nacional"
    assert outcome["financial_impact_snapshot"] == 200.0
    assert outcome["resolved_at"] is None
    assert outcome["reevaluated_at"] is None


async def test_list_outcomes_filters_by_status(client, auth_headers_a):
    outcome = await _create_outcome(client, auth_headers_a)
    resp_pendente = await client.get("/api/v1/insight-outcomes?status=pendente", headers=auth_headers_a)
    ids_pendente = {i["id"] for i in resp_pendente.json()["items"]}
    assert outcome["id"] in ids_pendente

    resp_resolvido = await client.get("/api/v1/insight-outcomes?status=resolvido", headers=auth_headers_a)
    ids_resolvido = {i["id"] for i in resp_resolvido.json()["items"]}
    assert outcome["id"] not in ids_resolvido


async def test_update_status_to_resolvido_sets_resolved_at(client, auth_headers_a):
    outcome = await _create_outcome(client, auth_headers_a)
    resp = await client.patch(
        f"/api/v1/insight-outcomes/{outcome['id']}", json={"status": "resolvido"}, headers=auth_headers_a
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "resolvido"
    assert body["resolved_at"] is not None


async def test_reopening_a_resolved_outcome_resets_reevaluation_cycle(client, auth_headers_a):
    outcome = await _create_outcome(client, auth_headers_a)
    await client.patch(f"/api/v1/insight-outcomes/{outcome['id']}", json={"status": "resolvido"}, headers=auth_headers_a)

    resp = await client.patch(
        f"/api/v1/insight-outcomes/{outcome['id']}", json={"status": "em_andamento"}, headers=auth_headers_a
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "em_andamento"
    assert body["resolved_at"] is None
    assert body["resolved_metric_value"] is None
    assert body["reevaluated_at"] is None


async def test_atendimento_cannot_create_outcome(client, admin_engine, tenant_a, auth_headers_a):
    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao@insight-outcome-test.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.post(
        "/api/v1/insight-outcomes",
        json={"source": "insight", "category": "agenda", "severity": "warning", "title": "X", "message": "Y"},
        headers=headers,
    )
    assert response.status_code == 403


async def test_assignee_can_update_own_status_but_not_reassign_or_change_due_date(
    client, admin_engine, tenant_a, auth_headers_a
):
    """Épico F1.3: "quem decide (gestor) não é quem executa" — a pessoa
    atribuída (mesmo sem _CAN_WRITE) marca o próprio progresso, mas não
    reatribui o item nem muda o prazo que o gestor definiu."""
    assignee = await _insert_user(admin_engine, tenant_id=tenant_a, email="faturista@insight-outcome-test.com", role="atendimento")
    outcome = await _create_outcome(client, auth_headers_a, assigned_to=assignee["id"])
    token = await _login(client, assignee["email"], assignee["password"])
    assignee_headers = {"Authorization": f"Bearer {token}"}

    ok_resp = await client.patch(
        f"/api/v1/insight-outcomes/{outcome['id']}",
        json={"status": "em_andamento", "resolution_note": "Já entrei em contato com o convênio."},
        headers=assignee_headers,
    )
    assert ok_resp.status_code == 200
    assert ok_resp.json()["status"] == "em_andamento"

    forbidden_resp = await client.patch(
        f"/api/v1/insight-outcomes/{outcome['id']}", json={"due_date": "2026-12-31"}, headers=assignee_headers
    )
    assert forbidden_resp.status_code == 403


async def test_non_assignee_without_manager_role_cannot_touch_outcome(client, admin_engine, tenant_a, auth_headers_a):
    other_user = await _insert_user(admin_engine, tenant_id=tenant_a, email="outra-recepcao@insight-outcome-test.com", role="atendimento")
    outcome = await _create_outcome(client, auth_headers_a)  # sem assigned_to
    token = await _login(client, other_user["email"], other_user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.patch(f"/api/v1/insight-outcomes/{outcome['id']}", json={"status": "em_andamento"}, headers=headers)
    assert response.status_code == 403


async def test_mine_endpoint_only_returns_own_assigned_items_even_for_atendimento(
    client, admin_engine, tenant_a, auth_headers_a
):
    assignee = await _insert_user(admin_engine, tenant_id=tenant_a, email="minha-fila@insight-outcome-test.com", role="atendimento")
    mine = await _create_outcome(client, auth_headers_a, title="Item atribuído a mim", assigned_to=assignee["id"])
    await _create_outcome(client, auth_headers_a, title="Item de outra pessoa")

    token = await _login(client, assignee["email"], assignee["password"])
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get("/api/v1/insight-outcomes/mine", headers=headers)
    assert resp.status_code == 200
    ids = {i["id"] for i in resp.json()["items"]}
    assert ids == {mine["id"]}


async def test_realized_summary_only_counts_resolved_and_reevaluated(client, auth_headers_a):
    outcome = await _create_outcome(client, auth_headers_a)
    await client.patch(f"/api/v1/insight-outcomes/{outcome['id']}", json={"status": "resolvido"}, headers=auth_headers_a)

    # Ainda não reavaliado pelo job — não deveria contar no resumo.
    resp = await client.get("/api/v1/insight-outcomes/realized-summary", headers=auth_headers_a)
    assert resp.status_code == 200
    body = resp.json()
    assert outcome["id"] not in {i["id"] for i in body["items"]}
    assert body["total_resolved_and_reevaluated"] == 0


# ---------------------------------------------------------------------
# Job de reavaliação (F1.2)
# ---------------------------------------------------------------------


async def test_reevaluation_job_fills_metric_value_when_problem_no_longer_appears(
    client, admin_engine, tenant_a, auth_headers_a
):
    """Sem nenhum dado real de faturamento por trás do título (a
    "perda" era só o snapshot do momento em que foi marcado), a fila
    recomputada não vai ter mais esse insight_key -> resolved_metric_value
    precisa vir 0.0 (resolvido de verdade), nunca None (que significaria
    'ainda não reavaliado')."""
    outcome = await _create_outcome(client, auth_headers_a)
    await client.patch(f"/api/v1/insight-outcomes/{outcome['id']}", json={"status": "resolvido"}, headers=auth_headers_a)

    # Backdata resolved_at pra além do corte do job (_REEVALUATION_DELAY_DAYS)
    # — direto no banco, simulando "14+ dias se passaram desde a resolução".
    old_resolved_at = datetime.now(timezone.utc) - timedelta(days=20)
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("UPDATE core.insight_outcomes SET resolved_at = :resolved_at WHERE id = :id"),
            {"resolved_at": old_resolved_at, "id": outcome["id"]},
        )

    await insight_outcome_reevaluation_job.run()

    resp = await client.get(f"/api/v1/insight-outcomes/{outcome['id']}", headers=auth_headers_a)
    assert resp.status_code == 200
    body = resp.json()
    assert body["reevaluated_at"] is not None
    assert body["resolved_metric_value"] == 0.0


async def test_reevaluation_job_ignores_outcomes_resolved_too_recently(client, admin_engine, tenant_a, auth_headers_a):
    outcome = await _create_outcome(client, auth_headers_a)
    await client.patch(f"/api/v1/insight-outcomes/{outcome['id']}", json={"status": "resolvido"}, headers=auth_headers_a)

    await insight_outcome_reevaluation_job.run()

    resp = await client.get(f"/api/v1/insight-outcomes/{outcome['id']}", headers=auth_headers_a)
    assert resp.json()["reevaluated_at"] is None
