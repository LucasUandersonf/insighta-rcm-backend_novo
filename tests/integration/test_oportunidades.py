"""
tests/integration/test_oportunidades.py

Oportunidades (Sala de Comando 2.0) — prova ponta a ponta que a função
SECURITY DEFINER cruza preço de contrato por convênio+procedimento
entre clínicas (via InsurancePlan.normalized_key), respeita o piso de
amostra mínima por grupo, e nunca expõe o preço de uma clínica
específica — só a mediana agregada.
"""
import uuid
from datetime import date

from sqlalchemy import text


async def _insert_tenant(admin_engine, *, trade_name: str) -> str:
    from tests.conftest import _insert_tenant as _do_insert

    return await _do_insert(admin_engine, trade_name=trade_name)


async def _seed_homologated_contract(
    admin_engine, tenant_id: str, *, normalized_key: str, tuss_code: str, agreed_price: float
) -> str:
    """Um convênio + um contrato homologado vigente + um item de preço —
    o mínimo para a clínica "ter" aquele procedimento contratado."""
    plan_id = str(uuid.uuid4())
    contract_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) "
                "VALUES (:id, :t, :name, :key)"
            ),
            {"id": plan_id, "t": tenant_id, "name": f"Convênio {normalized_key}", "key": normalized_key},
        )
        await conn.execute(
            text(
                "INSERT INTO core.contracts (id, tenant_id, insurance_plan_id, valid_from, status) "
                "VALUES (:id, :t, :plan, :valid_from, 'homologado')"
            ),
            {"id": contract_id, "t": tenant_id, "plan": plan_id, "valid_from": date(2020, 1, 1)},
        )
        await conn.execute(
            text(
                "INSERT INTO core.contract_items (tenant_id, contract_id, tuss_code, procedure_name, agreed_price) "
                "VALUES (:t, :c, :tuss, 'Consulta em consultório', :price)"
            ),
            {"t": tenant_id, "c": contract_id, "tuss": tuss_code, "price": agreed_price},
        )
    return plan_id


async def test_insufficient_cohort_omits_the_pair_entirely(client, auth_headers_a, admin_engine, tenant_a):
    await _seed_homologated_contract(
        admin_engine, tenant_a, normalized_key="unimed", tuss_code="10101012", agreed_price=100.0
    )
    # Só 1 outra clínica com o mesmo convênio+procedimento (abaixo do
    # piso de 3) — não deveria aparecer na resposta.
    other = await _insert_tenant(admin_engine, trade_name="Outra Clínica Isolada")
    await _seed_homologated_contract(
        admin_engine, other, normalized_key="unimed", tuss_code="10101012", agreed_price=200.0
    )

    response = await client.get("/api/v1/analytics/oportunidades", headers=auth_headers_a)
    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_computes_median_across_other_tenants_and_ranks_by_opportunity(
    client, auth_headers_a, admin_engine, tenant_a
):
    await _seed_homologated_contract(
        admin_engine, tenant_a, normalized_key="unimed", tuss_code="10101012", agreed_price=100.0
    )
    # 3 outras clínicas ativas, mesmo convênio+procedimento, preços bem
    # acima -> mediana da rede deveria ficar bem acima do preço da
    # clínica A, revelando uma oportunidade real.
    for price in (180.0, 200.0, 220.0):
        other = await _insert_tenant(admin_engine, trade_name=f"Clínica Rede {price}")
        await _seed_homologated_contract(
            admin_engine, other, normalized_key="unimed", tuss_code="10101012", agreed_price=price
        )

    response = await client.get("/api/v1/analytics/oportunidades", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["your_price"] == 100.0
    assert item["network_median_price"] == 200.0
    assert item["network_cohort_size"] == 3
    assert item["gap_value"] == 100.0
    # Só as chaves agregadas do schema — nenhum preço de clínica de terceiro.
    assert set(item.keys()) == {
        "insurance_plan_id",
        "plan_display_name",
        "tuss_code",
        "procedure_name",
        "your_price",
        "network_median_price",
        "network_cohort_size",
        "monthly_volume",
        "gap_value",
        "gap_pct",
        "estimated_monthly_opportunity",
    }


async def test_price_already_at_or_above_network_median_is_not_an_opportunity(
    client, auth_headers_a, admin_engine, tenant_a
):
    await _seed_homologated_contract(
        admin_engine, tenant_a, normalized_key="unimed", tuss_code="10101012", agreed_price=250.0
    )
    for price in (100.0, 120.0, 140.0):
        other = await _insert_tenant(admin_engine, trade_name=f"Clínica Rede {price}")
        await _seed_homologated_contract(
            admin_engine, other, normalized_key="unimed", tuss_code="10101012", agreed_price=price
        )

    response = await client.get("/api/v1/analytics/oportunidades", headers=auth_headers_a)
    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_draft_contract_is_never_considered_for_your_own_price(
    client, auth_headers_a, admin_engine, tenant_a
):
    """Contrato em 'rascunho'/'em_revisao' ainda não foi confirmado por
    humano — não deveria alimentar a comparação (mesmo critério de
    ContractItemRepository.find_agreed_price)."""
    plan_id = str(uuid.uuid4())
    contract_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) "
                "VALUES (:id, :t, 'Convênio Rascunho', 'unimed')"
            ),
            {"id": plan_id, "t": tenant_a},
        )
        await conn.execute(
            text(
                "INSERT INTO core.contracts (id, tenant_id, insurance_plan_id, valid_from, status) "
                "VALUES (:id, :t, :plan, :valid_from, 'rascunho')"
            ),
            {"id": contract_id, "t": tenant_a, "plan": plan_id, "valid_from": date(2020, 1, 1)},
        )
        await conn.execute(
            text(
                "INSERT INTO core.contract_items (tenant_id, contract_id, tuss_code, procedure_name, agreed_price) "
                "VALUES (:t, :c, '10101012', 'Consulta em consultório', 100.0)"
            ),
            {"t": tenant_a, "c": contract_id},
        )
    for price in (180.0, 200.0, 220.0):
        other = await _insert_tenant(admin_engine, trade_name=f"Clínica Rede {price}")
        await _seed_homologated_contract(
            admin_engine, other, normalized_key="unimed", tuss_code="10101012", agreed_price=price
        )

    response = await client.get("/api/v1/analytics/oportunidades", headers=auth_headers_a)
    assert response.status_code == 200
    assert response.json()["items"] == []
