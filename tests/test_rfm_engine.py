"""tests/test_rfm_engine.py — testes puros (sem banco), mesmo estilo de
test_patient_value_engine.py/test_no_show_risk_engine.py. Gaps Dossiê
Insighta RCM, item 4 (RFM completo — dimensão Valor)."""
from app.services.rfm_engine import (
    classify_segment,
    monetary_scores,
    score_frequency,
    score_recency,
)


def test_score_recency_boundaries():
    assert score_recency(0) == 5
    assert score_recency(30) == 5
    assert score_recency(31) == 4
    assert score_recency(90) == 4
    assert score_recency(91) == 3
    assert score_recency(180) == 3
    assert score_recency(181) == 2
    assert score_recency(365) == 2
    assert score_recency(366) == 1


def test_score_frequency_boundaries():
    assert score_frequency(1) == 1
    assert score_frequency(2) == 2
    assert score_frequency(3) == 3
    assert score_frequency(4) == 3
    assert score_frequency(5) == 4
    assert score_frequency(9) == 4
    assert score_frequency(10) == 5
    assert score_frequency(100) == 5


def test_monetary_scores_single_patient_lands_on_middle_score():
    # Sem base de comparação real (1 paciente só), não há "topo" nem
    # "fundo" pra apontar — cai no meio da régua, nunca inventa um
    # extremo.
    assert monetary_scores([500.0]) == [3]


def test_monetary_scores_empty_list():
    assert monetary_scores([]) == []


def test_monetary_scores_all_tied_lands_on_middle_score():
    assert monetary_scores([100.0, 100.0, 100.0]) == [3, 3, 3]


def test_monetary_scores_orders_by_rank_percentile():
    # 5 pacientes com receitas bem distintas -> score cresce com o rank.
    revenues = [100.0, 5000.0, 2000.0, 500.0, 3000.0]
    scores = monetary_scores(revenues)
    # Mesmo índice de entrada/saída
    assert len(scores) == 5
    # O maior valor (5000) tem o maior score; o menor (100) tem o menor.
    assert scores[revenues.index(5000.0)] == max(scores)
    assert scores[revenues.index(100.0)] == min(scores)
    # Ordem relativa preservada: quanto maior a receita, maior (ou igual) o score.
    pairs = sorted(zip(revenues, scores))
    assert [s for _, s in pairs] == sorted(s for _, s in pairs)


def test_classify_segment_campeoes():
    assert classify_segment(5, 5, 5) == "campeoes"
    assert classify_segment(4, 4, 4) == "campeoes"


def test_classify_segment_fieis_recent_frequent_not_yet_top_value():
    assert classify_segment(3, 5, 2) == "fieis"


def test_classify_segment_nao_pode_perder_high_value_gone_cold():
    assert classify_segment(1, 1, 5) == "nao_pode_perder"


def test_classify_segment_em_risco_used_to_be_frequent_gone_cold():
    assert classify_segment(2, 3, 2) == "em_risco"


def test_classify_segment_novos_recent_little_history():
    assert classify_segment(5, 1, 1) == "novos"


def test_classify_segment_hibernando_low_everything():
    assert classify_segment(1, 1, 1) == "hibernando"


def test_classify_segment_precisa_atencao_catch_all_middle():
    assert classify_segment(3, 3, 3) == "precisa_atencao"
