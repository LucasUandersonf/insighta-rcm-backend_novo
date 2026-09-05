"""
tests/test_no_show_risk_engine.py

Mesmo espírito de test_denial_risk_engine.py: instanciamos Appointment
como objeto Python comum, sem persistir nada, e testamos a regra de
negócio isolada da infraestrutura de banco.
"""
import statistics
import uuid
from datetime import datetime, timezone

import pytest

from app.models.appointment import Appointment
from app.services.no_show_risk_engine import MIN_PATIENTS_FOR_SUGGESTION, MIN_SPECIFIC_SAMPLES, assess, suggest_thresholds


def _appt(scheduled_at: datetime, status: str) -> Appointment:
    return Appointment(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        patient_id=uuid.uuid4(),
        scheduled_at=scheduled_at,
        status=status,
        created_at=datetime.now(timezone.utc),
    )


def test_no_history_is_indeterminado():
    result = assess([], candidate_scheduled_at=datetime(2026, 9, 7, 14, 0, tzinfo=timezone.utc))  # segunda-feira

    assert result.risk_level == "indeterminado"
    assert result.score is None
    assert result.sample_size == 0


def test_uses_general_rate_when_specific_pattern_has_too_few_samples():
    # Só 2 ocorrências na mesma combinação segunda+tarde (< MIN_SPECIFIC_SAMPLES=3)
    assert MIN_SPECIFIC_SAMPLES == 3
    history = [
        _appt(datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc), "no_show"),  # segunda tarde
        _appt(datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc), "completed"),  # segunda tarde
        _appt(datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc), "completed"),  # quinta manhã
        _appt(datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc), "completed"),  # quinta manhã
    ]

    result = assess(history, candidate_scheduled_at=datetime(2026, 9, 7, 14, 0, tzinfo=timezone.utc))  # segunda tarde

    assert result.used_specific_pattern is False
    assert result.sample_size == 4  # taxa geral, todas as 4 ocorrências
    assert result.score == 0.25  # 1 falta em 4
    assert result.risk_level == "medio"


def test_uses_specific_pattern_when_enough_samples_even_if_it_diverges_from_general_rate():
    # Paciente com histórico geral ótimo, mas 3 faltas em 3 segundas de tarde
    history = [
        _appt(datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc), "no_show"),
        _appt(datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc), "no_show"),
        _appt(datetime(2026, 9, 7, 14, 0, tzinfo=timezone.utc), "no_show"),
        _appt(datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc), "completed"),
        _appt(datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc), "completed"),
        _appt(datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc), "completed"),
    ]

    result = assess(history, candidate_scheduled_at=datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc))  # outra segunda de tarde

    assert result.used_specific_pattern is True
    assert result.sample_size == 3
    assert result.score == 1.0
    assert result.risk_level == "alto"


def test_cancelled_appointments_are_excluded_from_the_sample():
    history = [
        _appt(datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc), "cancelled"),
        _appt(datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc), "cancelled"),
        _appt(datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc), "completed"),
    ]

    result = assess(history, candidate_scheduled_at=datetime(2026, 9, 7, 14, 0, tzinfo=timezone.utc))

    assert result.sample_size == 1  # só o 'completed' conta; os 2 'cancelled' são ignorados
    assert result.score == 0.0
    assert result.risk_level == "baixo"


def test_suggest_thresholds_returns_none_below_minimum_patient_sample():
    assert MIN_PATIENTS_FOR_SUGGESTION == 10
    rates = [0.1] * (MIN_PATIENTS_FOR_SUGGESTION - 1)
    assert suggest_thresholds(rates) is None


def test_suggest_thresholds_uses_median_and_p85_of_the_clinics_own_distribution():
    # 20 pacientes, taxas de 0.00 a 0.19 (passo de 0.01) — distribuição
    # conhecida o bastante para prever mediana e P85 na mão.
    rates = [round(i * 0.01, 2) for i in range(20)]

    suggestion = suggest_thresholds(rates)

    assert suggestion is not None
    assert suggestion.sample_size == 20
    assert suggestion.low_threshold == pytest.approx(statistics.median(rates), abs=0.001)
    assert suggestion.medium_threshold > suggestion.low_threshold


def test_suggest_thresholds_never_returns_medium_at_or_below_low():
    # Distribuição sem variação nenhuma: mediana == P85 == mesmo valor —
    # a função precisa se auto-corrigir para manter medium > low (mesma
    # regra exigida por TenantService.update_own_tenant).
    rates = [0.2] * 15

    suggestion = suggest_thresholds(rates)

    assert suggestion is not None
    assert suggestion.medium_threshold > suggestion.low_threshold


if __name__ == "__main__":
    test_no_history_is_indeterminado()
    test_uses_general_rate_when_specific_pattern_has_too_few_samples()
    test_uses_specific_pattern_when_enough_samples_even_if_it_diverges_from_general_rate()
    test_cancelled_appointments_are_excluded_from_the_sample()
    test_suggest_thresholds_returns_none_below_minimum_patient_sample()
    test_suggest_thresholds_uses_median_and_p85_of_the_clinics_own_distribution()
    test_suggest_thresholds_never_returns_medium_at_or_below_low()
    print("Testes de no_show_risk_engine passaram.")
