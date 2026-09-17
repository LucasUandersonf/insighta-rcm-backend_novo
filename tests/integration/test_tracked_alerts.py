"""
tests/integration/test_tracked_alerts.py

Memória contínua dia-a-dia (Roadmap "Rumo à Nota 9", Fase 3) — pedido
direto do usuário: "se algo foi ajustado, no outro dia a IA já menciona
que o erro foi corrigido". Ponta a ponta via HTTP: uma situação sinalizada
por um insight precisa virar uma linha em core.tracked_alerts, e precisa
ser marcada resolvida quando a causa desaparece — sem depender da IA
(a chamada de rede é mockada, mesma técnica de test_executive_narrative.py).
"""
from datetime import date

from sqlalchemy import text

from tests.integration.test_analytics import _seed_revenue_leak_billing


async def test_smart_insights_tracks_a_new_situation_in_tracked_alerts(client, auth_headers_a, admin_engine, tenant_a):
    billing = await _seed_revenue_leak_billing(client, admin_engine, tenant_a, auth_headers_a, agreed_value=300.0, charged_value=250.0)
    assert billing  # a fatura existe e está abaixo do combinado

    response = await client.get("/api/v1/analytics/smart-insights", headers=auth_headers_a)
    assert response.status_code == 200

    async with admin_engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT resolved_date, first_detected_date, last_detected_date FROM core.tracked_alerts "
                    "WHERE tenant_id = :t AND title = :title"
                ),
                {"t": tenant_a, "title": "Você está cobrando menos do que devia de alguns convênios"},
            )
        ).mappings().one()

    assert row["resolved_date"] is None
    assert row["first_detected_date"] == date.today()
    assert row["last_detected_date"] == date.today()


async def test_smart_insights_marks_a_tracked_alert_resolved_once_the_cause_is_fixed(
    client, auth_headers_a, admin_engine, tenant_a
):
    billing = await _seed_revenue_leak_billing(client, admin_engine, tenant_a, auth_headers_a, agreed_value=300.0, charged_value=250.0)

    first = await client.get("/api/v1/analytics/smart-insights", headers=auth_headers_a)
    assert any(i["title"] == "Você está cobrando menos do que devia de alguns convênios" for i in first.json()["insights"])

    # "o sistema manda a atualização de novos dados" — corrige a cobrança
    # pra bater com o valor combinado no contrato (financial_hole some).
    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE core.billing SET charged_value = 300.0 WHERE id = :id"), {"id": billing["id"]})

    second = await client.get("/api/v1/analytics/smart-insights", headers=auth_headers_a)
    assert not any(
        i["title"] == "Você está cobrando menos do que devia de alguns convênios" for i in second.json()["insights"]
    )

    async with admin_engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT resolved_date FROM core.tracked_alerts WHERE tenant_id = :t AND title = :title"),
                {"t": tenant_a, "title": "Você está cobrando menos do que devia de alguns convênios"},
            )
        ).mappings().one()
    assert row["resolved_date"] == date.today()


async def test_executive_narrative_mentions_a_recently_resolved_situation(
    client, auth_headers_a, admin_engine, tenant_a, monkeypatch
):
    """Fim a fim (com a chamada de IA mockada, mesma técnica de
    test_executive_narrative.py): o texto do prompt de fato carrega a
    situação corrigida — o que a IA faz com essa frase já é coberto pelos
    testes puros de build_narrative_prompt (test_executive_narrative_service.py)."""
    from app.services import executive_narrative_service as narrative_module

    monkeypatch.setattr(narrative_module.settings, "ANTHROPIC_API_KEY", "fake-key-de-teste")
    captured_prompts: list[str] = []

    async def _fake_generate(self, facts_text: str) -> str:
        captured_prompts.append(facts_text)
        return "Resumo de teste."

    monkeypatch.setattr(narrative_module.AnthropicNarrativeGenerator, "generate", _fake_generate)

    billing = await _seed_revenue_leak_billing(client, admin_engine, tenant_a, auth_headers_a, agreed_value=300.0, charged_value=250.0)

    # Primeira visita do dia: detecta e sinaliza a situação (ainda ativa) —
    # o resumo gerado agora não deveria mencionar nenhuma correção.
    first_narrative = await client.get("/api/v1/analytics/executive-narrative", headers=auth_headers_a)
    assert first_narrative.status_code == 200
    assert "corrigidas desde a última checagem" not in captured_prompts[0]

    # Corrige a causa e limpa o cache diário da narrativa (mesmo truque de
    # test_executive_narrative_generates_and_caches_once_per_day: forçar
    # uma nova geração no MESMO dia, sem esperar a virada de data real).
    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE core.billing SET charged_value = 300.0 WHERE id = :id"), {"id": billing["id"]})
        await conn.execute(text("DELETE FROM core.executive_narratives WHERE tenant_id = :t"), {"t": tenant_a})

    second_narrative = await client.get("/api/v1/analytics/executive-narrative", headers=auth_headers_a)
    assert second_narrative.status_code == 200
    assert len(captured_prompts) == 2
    assert "Situações que estavam sinalizadas e foram corrigidas desde a última checagem:" in captured_prompts[1]
    assert "Você está cobrando menos do que devia de alguns convênios" in captured_prompts[1]
