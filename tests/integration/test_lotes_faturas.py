"""
tests/integration/test_lotes_faturas.py

Fase 2 do plano de adequação ao fluxo real de mercado (Agendamento ->
Atendimento -> Faturamento — ver conversa/PLANO_ADEQUACAO_TISS.md):
Lote agrupa Guias do mesmo convênio+tipo (confirmado de forma idêntica
em 3 ERPs do mercado — Moderna, Feegow, iClinic); Fatura agrupa Lotes
FECHADOS para envio/baixa.
"""
import uuid

from sqlalchemy import text


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Unimed Nacional", normalized_key="unimed_nacional") -> str:
    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :n, :k)"),
            {"id": plan_id, "t": tenant_id, "n": display_name, "k": normalized_key},
        )
    return plan_id


async def _create_guia(client, headers, plan_id, tipo="consulta") -> dict:
    resp = await client.post("/api/v1/guias", json={"insurance_plan_id": plan_id, "tipo": tipo}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_full_lote_lifecycle_open_add_close(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)

    lote_resp = await client.post(
        "/api/v1/lotes", json={"insurance_plan_id": plan_id, "tipo": "consulta"}, headers=auth_headers_a
    )
    assert lote_resp.status_code == 201, lote_resp.text
    lote = lote_resp.json()
    assert lote["status"] == "aberto"
    assert lote["fatura_id"] is None

    guia1 = await _create_guia(client, auth_headers_a, plan_id, tipo="consulta")
    guia2 = await _create_guia(client, auth_headers_a, plan_id, tipo="consulta")

    add1_resp = await client.post(f"/api/v1/lotes/{lote['id']}/guias/{guia1['id']}", headers=auth_headers_a)
    assert add1_resp.status_code == 200, add1_resp.text
    assert add1_resp.json()["lote_id"] == lote["id"]

    add2_resp = await client.post(f"/api/v1/lotes/{lote['id']}/guias/{guia2['id']}", headers=auth_headers_a)
    assert add2_resp.status_code == 200

    guias_resp = await client.get(f"/api/v1/lotes/{lote['id']}/guias", headers=auth_headers_a)
    assert guias_resp.status_code == 200
    assert {g["id"] for g in guias_resp.json()} == {guia1["id"], guia2["id"]}

    fechar_resp = await client.post(f"/api/v1/lotes/{lote['id']}/fechar", headers=auth_headers_a)
    assert fechar_resp.status_code == 200
    assert fechar_resp.json()["status"] == "fechado"
    assert fechar_resp.json()["closed_at"] is not None


async def test_cannot_close_empty_lote(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    lote_resp = await client.post(
        "/api/v1/lotes", json={"insurance_plan_id": plan_id, "tipo": "consulta"}, headers=auth_headers_a
    )
    lote_id = lote_resp.json()["id"]

    fechar_resp = await client.post(f"/api/v1/lotes/{lote_id}/fechar", headers=auth_headers_a)
    assert fechar_resp.status_code == 400


async def test_cannot_add_guia_with_different_convenio_or_tipo(client, auth_headers_a, admin_engine, tenant_a):
    plan_a = await _create_insurance_plan(admin_engine, tenant_a, "Unimed Nacional", "unimed_nacional")
    plan_b = await _create_insurance_plan(admin_engine, tenant_a, "SulAmérica", "sulamerica")

    lote_resp = await client.post(
        "/api/v1/lotes", json={"insurance_plan_id": plan_a, "tipo": "consulta"}, headers=auth_headers_a
    )
    lote_id = lote_resp.json()["id"]

    # Convênio diferente do lote
    guia_outro_convenio = await _create_guia(client, auth_headers_a, plan_b, tipo="consulta")
    resp_convenio = await client.post(f"/api/v1/lotes/{lote_id}/guias/{guia_outro_convenio['id']}", headers=auth_headers_a)
    assert resp_convenio.status_code == 400

    # Mesmo convênio, tipo diferente do lote
    guia_outro_tipo = await _create_guia(client, auth_headers_a, plan_a, tipo="sadt")
    resp_tipo = await client.post(f"/api/v1/lotes/{lote_id}/guias/{guia_outro_tipo['id']}", headers=auth_headers_a)
    assert resp_tipo.status_code == 400


async def test_cannot_add_guia_already_in_another_lote(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    lote1 = (
        await client.post("/api/v1/lotes", json={"insurance_plan_id": plan_id, "tipo": "consulta"}, headers=auth_headers_a)
    ).json()
    lote2 = (
        await client.post("/api/v1/lotes", json={"insurance_plan_id": plan_id, "tipo": "consulta"}, headers=auth_headers_a)
    ).json()
    guia = await _create_guia(client, auth_headers_a, plan_id, tipo="consulta")

    ok_resp = await client.post(f"/api/v1/lotes/{lote1['id']}/guias/{guia['id']}", headers=auth_headers_a)
    assert ok_resp.status_code == 200

    conflict_resp = await client.post(f"/api/v1/lotes/{lote2['id']}/guias/{guia['id']}", headers=auth_headers_a)
    assert conflict_resp.status_code == 400


async def test_cannot_edit_a_closed_lote(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    lote = (
        await client.post("/api/v1/lotes", json={"insurance_plan_id": plan_id, "tipo": "consulta"}, headers=auth_headers_a)
    ).json()
    guia = await _create_guia(client, auth_headers_a, plan_id, tipo="consulta")
    await client.post(f"/api/v1/lotes/{lote['id']}/guias/{guia['id']}", headers=auth_headers_a)
    await client.post(f"/api/v1/lotes/{lote['id']}/fechar", headers=auth_headers_a)

    guia2 = await _create_guia(client, auth_headers_a, plan_id, tipo="consulta")
    add_resp = await client.post(f"/api/v1/lotes/{lote['id']}/guias/{guia2['id']}", headers=auth_headers_a)
    assert add_resp.status_code == 400

    remove_resp = await client.delete(f"/api/v1/lotes/{lote['id']}/guias/{guia['id']}", headers=auth_headers_a)
    assert remove_resp.status_code == 400

    refechar_resp = await client.post(f"/api/v1/lotes/{lote['id']}/fechar", headers=auth_headers_a)
    assert refechar_resp.status_code == 400


async def test_full_fatura_lifecycle_create_and_baixar(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)

    lote1 = (
        await client.post("/api/v1/lotes", json={"insurance_plan_id": plan_id, "tipo": "consulta"}, headers=auth_headers_a)
    ).json()
    guia1 = await _create_guia(client, auth_headers_a, plan_id, tipo="consulta")
    await client.post(f"/api/v1/lotes/{lote1['id']}/guias/{guia1['id']}", headers=auth_headers_a)
    await client.post(f"/api/v1/lotes/{lote1['id']}/fechar", headers=auth_headers_a)

    # Fatura pode agrupar mais de um lote do MESMO convênio (tipos
    # diferentes) — ver DECISÃO em app/sql/016_lotes_faturas.sql.
    lote2 = (
        await client.post("/api/v1/lotes", json={"insurance_plan_id": plan_id, "tipo": "sadt"}, headers=auth_headers_a)
    ).json()
    guia2 = await _create_guia(client, auth_headers_a, plan_id, tipo="sadt")
    await client.post(f"/api/v1/lotes/{lote2['id']}/guias/{guia2['id']}", headers=auth_headers_a)
    await client.post(f"/api/v1/lotes/{lote2['id']}/fechar", headers=auth_headers_a)

    fatura_resp = await client.post(
        "/api/v1/faturas",
        json={"lote_ids": [lote1["id"], lote2["id"]], "serie": "NF", "numero": "000123"},
        headers=auth_headers_a,
    )
    assert fatura_resp.status_code == 201, fatura_resp.text
    fatura = fatura_resp.json()
    assert fatura["status"] == "emitida"
    assert fatura["insurance_plan_id"] == plan_id

    # Os dois lotes viram "faturado" e ganham fatura_id
    lote1_after = (await client.get(f"/api/v1/lotes/{lote1['id']}", headers=auth_headers_a)).json()
    lote2_after = (await client.get(f"/api/v1/lotes/{lote2['id']}", headers=auth_headers_a)).json()
    assert lote1_after["status"] == "faturado"
    assert lote1_after["fatura_id"] == fatura["id"]
    assert lote2_after["status"] == "faturado"
    assert lote2_after["fatura_id"] == fatura["id"]

    baixar_resp = await client.post(
        f"/api/v1/faturas/{fatura['id']}/baixar", json={"valor_recebido": 250.0}, headers=auth_headers_a
    )
    assert baixar_resp.status_code == 200, baixar_resp.text
    settled = baixar_resp.json()
    assert settled["status"] == "paga"
    assert settled["valor_recebido"] == 250.0
    assert settled["data_recebimento"] is not None


async def test_baixar_fatura_with_is_partial_sets_status(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    lote = (
        await client.post("/api/v1/lotes", json={"insurance_plan_id": plan_id, "tipo": "consulta"}, headers=auth_headers_a)
    ).json()
    guia = await _create_guia(client, auth_headers_a, plan_id, tipo="consulta")
    await client.post(f"/api/v1/lotes/{lote['id']}/guias/{guia['id']}", headers=auth_headers_a)
    await client.post(f"/api/v1/lotes/{lote['id']}/fechar", headers=auth_headers_a)

    fatura = (await client.post("/api/v1/faturas", json={"lote_ids": [lote["id"]]}, headers=auth_headers_a)).json()

    baixar_resp = await client.post(
        f"/api/v1/faturas/{fatura['id']}/baixar",
        json={"valor_recebido": 80.0, "is_partial": True},
        headers=auth_headers_a,
    )
    assert baixar_resp.status_code == 200
    assert baixar_resp.json()["status"] == "parcialmente_paga"


async def test_fatura_rejects_lotes_still_open(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    lote = (
        await client.post("/api/v1/lotes", json={"insurance_plan_id": plan_id, "tipo": "consulta"}, headers=auth_headers_a)
    ).json()
    guia = await _create_guia(client, auth_headers_a, plan_id, tipo="consulta")
    await client.post(f"/api/v1/lotes/{lote['id']}/guias/{guia['id']}", headers=auth_headers_a)
    # NÃO fechado de propósito

    resp = await client.post("/api/v1/faturas", json={"lote_ids": [lote["id"]]}, headers=auth_headers_a)
    assert resp.status_code == 400


async def test_fatura_rejects_lotes_from_different_convenios(client, auth_headers_a, admin_engine, tenant_a):
    plan_a = await _create_insurance_plan(admin_engine, tenant_a, "Unimed Nacional", "unimed_nacional")
    plan_b = await _create_insurance_plan(admin_engine, tenant_a, "SulAmérica", "sulamerica")

    lote_a = (
        await client.post("/api/v1/lotes", json={"insurance_plan_id": plan_a, "tipo": "consulta"}, headers=auth_headers_a)
    ).json()
    guia_a = await _create_guia(client, auth_headers_a, plan_a, tipo="consulta")
    await client.post(f"/api/v1/lotes/{lote_a['id']}/guias/{guia_a['id']}", headers=auth_headers_a)
    await client.post(f"/api/v1/lotes/{lote_a['id']}/fechar", headers=auth_headers_a)

    lote_b = (
        await client.post("/api/v1/lotes", json={"insurance_plan_id": plan_b, "tipo": "consulta"}, headers=auth_headers_a)
    ).json()
    guia_b = await _create_guia(client, auth_headers_a, plan_b, tipo="consulta")
    await client.post(f"/api/v1/lotes/{lote_b['id']}/guias/{guia_b['id']}", headers=auth_headers_a)
    await client.post(f"/api/v1/lotes/{lote_b['id']}/fechar", headers=auth_headers_a)

    resp = await client.post("/api/v1/faturas", json={"lote_ids": [lote_a["id"], lote_b["id"]]}, headers=auth_headers_a)
    assert resp.status_code == 400


async def test_fatura_rejects_lote_from_other_tenant(client, auth_headers_a, auth_headers_b, admin_engine, tenant_a, tenant_b):
    plan_id_b = await _create_insurance_plan(admin_engine, tenant_b)
    lote_b = (
        await client.post("/api/v1/lotes", json={"insurance_plan_id": plan_id_b, "tipo": "consulta"}, headers=auth_headers_b)
    ).json()
    guia_b = await _create_guia(client, auth_headers_b, plan_id_b, tipo="consulta")
    await client.post(f"/api/v1/lotes/{lote_b['id']}/guias/{guia_b['id']}", headers=auth_headers_b)
    await client.post(f"/api/v1/lotes/{lote_b['id']}/fechar", headers=auth_headers_b)

    # Clínica A tenta faturar um lote da Clínica B — RLS esconde o lote,
    # resultado é "não encontrado", nunca um dado de outro tenant vazando.
    resp = await client.post("/api/v1/faturas", json={"lote_ids": [lote_b["id"]]}, headers=auth_headers_a)
    assert resp.status_code == 404
