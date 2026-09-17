"""
tests/test_executive_narrative_service.py

`build_narrative_prompt` é PURA (não toca rede nem banco) — mesmo
princípio de test_contract_extraction_service.py: só monta o texto de
fatos pré-formatados que vai para a IA, testável isoladamente da
chamada de rede (AnthropicNarrativeGenerator).
"""
from datetime import date

from app.services.executive_narrative_service import NarrativeFacts, build_narrative_prompt


def _facts(**overrides) -> NarrativeFacts:
    defaults = dict(
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 7),
        total_billed=12345.67,
        financial_hole=234.0,
        payment_gap=120.5,
        denial_at_risk_value=890.0,
        avg_days_to_receive=62.0,
        insight_lines=[],
    )
    defaults.update(overrides)
    return NarrativeFacts(**defaults)


def test_prompt_includes_the_period_and_every_formatted_kpi():
    prompt = build_narrative_prompt(_facts())
    assert "01/09 a 07/09/2026" in prompt
    assert "R$ 12,345.67" in prompt
    assert "R$ 234.00" in prompt
    assert "R$ 120.50" in prompt
    assert "R$ 890.00" in prompt
    assert "62 dias" in prompt


def test_prompt_omits_payment_lag_line_when_none():
    # Sem billing conciliado no período (mesmo critério de
    # ExecutiveSummaryResponse.avg_days_to_receive) — nunca inventa "0
    # dias", só omite a linha inteira.
    prompt = build_narrative_prompt(_facts(avg_days_to_receive=None))
    assert "Prazo médio de recebimento" not in prompt


def test_prompt_includes_top_insights_verbatim_and_labeled_by_importance():
    prompt = build_narrative_prompt(
        _facts(insight_lines=["Convênio X demorando para pagar: mensagem A", "Agenda de segunda caiu: mensagem B"])
    )
    assert "do mais para o menos importante" in prompt
    assert "- Convênio X demorando para pagar: mensagem A" in prompt
    assert "- Agenda de segunda caiu: mensagem B" in prompt
    # Ordem preservada (generate_insights já ordena por impacto — este
    # módulo nunca reordena).
    assert prompt.index("mensagem A") < prompt.index("mensagem B")


def test_prompt_caps_insights_at_four_even_when_more_are_given():
    many = [f"Insight {i}: mensagem {i}" for i in range(10)]
    prompt = build_narrative_prompt(_facts(insight_lines=many))
    assert prompt.count("- Insight") == 4
    assert "Insight 0" in prompt
    assert "Insight 4" not in prompt


def test_prompt_has_no_insights_section_when_there_are_none():
    prompt = build_narrative_prompt(_facts(insight_lines=[]))
    assert "Insights identificados" not in prompt


def test_prompt_has_no_resolved_section_when_nothing_was_resolved():
    prompt = build_narrative_prompt(_facts(resolved_since_yesterday_titles=[]))
    assert "corrigidas desde a última checagem" not in prompt


def test_prompt_includes_resolved_situations_since_last_check():
    # Memória contínua dia-a-dia (Roadmap "Rumo à Nota 9", Fase 3) — pedido
    # direto do usuário: o texto precisa poder dizer "isso que eu avisei já
    # foi corrigido", não só apontar problema novo.
    prompt = build_narrative_prompt(
        _facts(resolved_since_yesterday_titles=["Você está cobrando menos do que devia de alguns convênios"])
    )
    assert "Situações que estavam sinalizadas e foram corrigidas desde a última checagem:" in prompt
    assert "- Você está cobrando menos do que devia de alguns convênios" in prompt
