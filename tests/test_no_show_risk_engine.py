"""
tests/test_no_show_risk_engine.py

Mesmo espírito de test_denial_risk_engine.py: instanciamos Appointment
como objeto Python comum, sem persistir nada, e testamos a regra de
negócio isolada da infraestrutura de banco.
"""
import uuid
from datetime import datetime, timezone

from app.models.appointment import Appointment
from app.services.no_show_risk_engine import MIN_SPECIFIC_SAMPLES, assess


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


if __name__ == "__main__":
    test_no_history_is_indeterminado()
    test_uses_general_rate_when_specific_pattern_has_too_few_samples()
    test_uses_specific_pattern_when_enough_samples_even_if_it_diverges_from_general_rate()
    test_cancelled_appointments_are_excluded_from_the_sample()
    print("Testes de no_show_risk_engine passaram.")
