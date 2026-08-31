"""
tests/test_appeal_deadline_calculator.py

Função pura, sem dependência de banco — mesma técnica de
test_denial_risk_engine.py.
"""
from datetime import date

from app.services.appeal_deadline_calculator import compute_deadline


def test_uses_company_deadline_when_present():
    result = compute_deadline(date(2026, 1, 1), company_deadline_days=15, default_deadline_days=30)
    assert result == date(2026, 1, 16)


def test_falls_back_to_default_when_company_deadline_is_none():
    result = compute_deadline(date(2026, 1, 1), company_deadline_days=None, default_deadline_days=30)
    assert result == date(2026, 1, 31)


def test_company_deadline_of_zero_is_still_used_over_default():
    # company_deadline_days=0 é "operadora não dá NENHUM prazo" (um dado
    # real, ainda que estranho) — diferente de None ("tenant não
    # configurou ainda"). Zero explícito deveria disparar erro de
    # validação, não silenciosamente virar o default.
    try:
        compute_deadline(date(2026, 1, 1), company_deadline_days=0, default_deadline_days=30)
        assert False, "deveria ter levantado ValueError"
    except ValueError:
        pass


def test_negative_default_raises():
    try:
        compute_deadline(date(2026, 1, 1), company_deadline_days=None, default_deadline_days=-5)
        assert False, "deveria ter levantado ValueError"
    except ValueError:
        pass


def test_crosses_month_boundary_correctly():
    result = compute_deadline(date(2026, 1, 20), company_deadline_days=30, default_deadline_days=30)
    assert result == date(2026, 2, 19)
