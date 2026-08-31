from app.services.report_calculations import average_utilization, compute_roi_pct


def test_roi_positive():
    assert compute_roi_pct(spend=1000, revenue=1500) == 0.5


def test_roi_negative_when_revenue_below_spend():
    assert compute_roi_pct(spend=1000, revenue=800) == -0.2


def test_roi_none_when_no_spend():
    assert compute_roi_pct(spend=0, revenue=500) is None
    assert compute_roi_pct(spend=-10, revenue=500) is None


def test_average_utilization_basic():
    assert average_utilization([0.5, 0.7, 0.9]) == (0.5 + 0.7 + 0.9) / 3


def test_average_utilization_empty_is_none():
    assert average_utilization([]) is None


if __name__ == "__main__":
    test_roi_positive()
    test_roi_negative_when_revenue_below_spend()
    test_roi_none_when_no_spend()
    test_average_utilization_basic()
    test_average_utilization_empty_is_none()
    print("Testes de report_calculations passaram.")
