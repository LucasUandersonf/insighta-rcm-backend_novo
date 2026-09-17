"""
tests/integration/test_executive_narrative.py

Resumo executivo narrado por IA (Sala de Comando) — pedido direto do
usuário: "a IA seria o Jarvis pegando os nossos cálculos e transformando
em texto explicativo". Mesma técnica de test_contract_extraction_reconciliation.py:
mocka só a fronteira de rede (AnthropicNarrativeGenerator.generate) —
tudo antes disso (cálculo dos KPIs/insights reais, cache diário via
Postgres, RLS) roda 100% real.
"""
from datetime import date

import pytest
from sqlalchemy import text

from app.services import executive_narrative_service as narrative_module
from tests.integration.test_analytics import _seed_revenue_leak_billing


async def test_executive_narrative_is_none_when_ai_not_configured(client, auth_headers_a):
    """Degradação graciosa (mesmo princípio de SENTRY_DSN/SMTP ausentes):
    sem ANTHROPIC_API_KEY, a Sala de Comando continua respondendo 200,
    só sem o resumo — nunca quebra a tela por causa disso."""
    response = await client.get("/api/v1/analytics/executive-narrative", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["narrative"] is None
    assert body["period_start"] is not None
    assert body["period_end"] is not None
    assert body["top_priorities"] == []
    assert body["recently_resolved"] == []


async def test_executive_narrative_exposes_top_priorities_for_the_home_briefing(
    client, auth_headers_a, admin_engine, tenant_a
):
    """Home estilo Jarvis (Roadmap "Rumo à Nota 9", Fase 1): a Home não
    repete o feed inteiro de insights, só as prioridades — mesmo dado que
    a Sala de Comando já calcula (ver AnalyticsService.get_smart_insights),
    exposto aqui pra a Home não precisar de outra chamada nem de outro
    ranking. `_seed_revenue_leak_billing` grava a fatura com created_at =
    agora, dentro da janela fixa de 7 dias da narrativa."""
    await _seed_revenue_leak_billing(client, admin_engine, tenant_a, auth_headers_a, agreed_value=300.0, charged_value=250.0)

    response = await client.get("/api/v1/analytics/executive-narrative", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert len(body["top_priorities"]) <= 3
    assert any(
        p["title"] == "Você está cobrando menos do que devia de alguns convênios" and p["financial_impact"] == 50.0
        for p in body["top_priorities"]
    )


@pytest.fixture
def _fake_narrative_ai(monkeypatch):
    """Substitui SÓ a chamada de rede real à Anthropic — a IA "de
    mentirinha" sempre devolve o mesmo texto fixo, contando quantas
    vezes foi chamada (para provar o cache diário abaixo)."""
    monkeypatch.setattr(narrative_module.settings, "ANTHROPIC_API_KEY", "fake-key-de-teste")
    calls = {"count": 0}

    async def _fake_generate(self, facts_text: str) -> str:
        calls["count"] += 1
        return "A clínica faturou bem esta semana, mas vale ficar de olho no prazo de recebimento."

    monkeypatch.setattr(narrative_module.AnthropicNarrativeGenerator, "generate", _fake_generate)
    return calls


async def test_executive_narrative_generates_and_caches_once_per_day(client, auth_headers_a, admin_engine, tenant_a, _fake_narrative_ai):
    first = await client.get("/api/v1/analytics/executive-narrative", headers=auth_headers_a)
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["narrative"] == "A clínica faturou bem esta semana, mas vale ficar de olho no prazo de recebimento."
    assert first_body["generated_at"] is not None
    assert _fake_narrative_ai["count"] == 1

    # Segunda visita no MESMO dia: devolve o texto já gravado, sem
    # chamar a IA de novo — mesmo cache diário de propósito (ver DECISÃO
    # em app/sql/038_executive_narratives.sql).
    second = await client.get("/api/v1/analytics/executive-narrative", headers=auth_headers_a)
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["narrative"] == first_body["narrative"]
    assert _fake_narrative_ai["count"] == 1  # não chamou a IA de novo

    async with admin_engine.begin() as conn:
        count = await conn.scalar(
            text("SELECT COUNT(*) FROM core.executive_narratives WHERE tenant_id = :t"), {"t": tenant_a}
        )
    assert count == 1


async def test_executive_narrative_isolates_cache_between_tenants(
    client, auth_headers_a, auth_headers_b, _fake_narrative_ai
):
    await client.get("/api/v1/analytics/executive-narrative", headers=auth_headers_a)
    assert _fake_narrative_ai["count"] == 1

    # Outro tenant no mesmo dia: cache é POR TENANT (RLS/UNIQUE
    # (tenant_id, digest_date)) — precisa gerar a própria narrativa, não
    # reaproveitar a do tenant A.
    response_b = await client.get("/api/v1/analytics/executive-narrative", headers=auth_headers_b)
    assert response_b.status_code == 200
    assert _fake_narrative_ai["count"] == 2


async def test_executive_narrative_gracefully_returns_none_when_generation_fails(client, auth_headers_a, monkeypatch):
    monkeypatch.setattr(narrative_module.settings, "ANTHROPIC_API_KEY", "fake-key-de-teste")

    async def _failing_generate(self, facts_text: str):
        raise narrative_module.NarrativeGenerationError("Falha simulada de rede.")

    monkeypatch.setattr(narrative_module.AnthropicNarrativeGenerator, "generate", _failing_generate)

    response = await client.get("/api/v1/analytics/executive-narrative", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["narrative"] is None
    assert body["period_end"] == date.today().isoformat()
