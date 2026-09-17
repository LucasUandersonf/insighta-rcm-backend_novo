"""tests/test_threshold_calibration.py — testes puros (sem banco) do
núcleo estatístico compartilhado (Épico F2.1 do Plano Diretor —
"Calibração por especialidade/porte"). Mesmo estilo de
test_no_show_risk_engine.py."""
from app.services.threshold_calibration import compute_percentile, compute_percentile_pair, suggest_single_threshold


def test_compute_percentile_pair_below_min_sample_returns_none():
    assert compute_percentile_pair([0.1, 0.2], min_sample=5, high_percentile=85) is None


def test_compute_percentile_pair_returns_median_and_high_percentile():
    values = [float(v) / 100 for v in range(1, 21)]  # 0.01 .. 0.20, 20 pontos
    pair = compute_percentile_pair(values, min_sample=10, high_percentile=85)
    assert pair is not None
    assert pair.sample_size == 20
    assert abs(pair.median - 0.105) < 1e-9
    # P85 de 20 pontos uniformes 0.01..0.20
    assert pair.high > pair.median


def test_compute_percentile_single_value_returns_that_value():
    assert compute_percentile([0.5], percentile=90) == 0.5


def test_suggest_single_threshold_below_min_sample_returns_none():
    assert suggest_single_threshold([0.1, 0.2, 0.3], min_sample=6, percentile=90) is None


def test_suggest_single_threshold_returns_value_and_sample_size():
    values = [float(v) / 100 for v in range(1, 13)]  # 12 meses
    result = suggest_single_threshold(values, min_sample=6, percentile=90)
    assert result is not None
    value, sample_size = result
    assert sample_size == 12
    assert value >= max(values) * 0.8  # P90 deveria ficar perto do topo da distribuição
