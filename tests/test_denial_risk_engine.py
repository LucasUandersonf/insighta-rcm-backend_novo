"""
tests/test_denial_risk_engine.py

Demonstra na prática a vantagem de ter denial_risk_engine.py como função
pura, sem dependência de banco: instanciamos Appointment/ContractItem
como objetos Python comuns (sem persistir nada) e testamos a regra de
negócio isoladamente, em milissegundos, sem subir Postgres nem FastAPI.
"""
import uuid
from datetime import datetime, timezone

from app.models.appointment import Appointment
from app.models.contract_item import ContractItem
from app.services.denial_risk_engine import assess


def _make_appointment(*, cid_code: str | None = "J06", procedure_code: str | None = "10101012") -> Appointment:
    return Appointment(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        patient_id=uuid.uuid4(),
        insurance_plan_id=uuid.uuid4(),
        scheduled_at=datetime.now(timezone.utc),
        status="completed",
        procedure_code=procedure_code,
        cid_code=cid_code,
        created_at=datetime.now(timezone.utc),
    )


def _make_contract_item(agreed_price: float = 150.00) -> ContractItem:
    return ContractItem(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        contract_id=uuid.uuid4(),
        tuss_code="10101012",
        agreed_price=agreed_price,
        created_at=datetime.now(timezone.utc),
    )


def test_missing_cid_is_high_risk_and_blocks_submission():
    appointment = _make_appointment(cid_code=None)
    contract_item = _make_contract_item()

    result = assess(appointment, contract_item, charged_value=150.00)

    assert result.level == "high"
    assert "missing_cid" in result.reasons
    assert result.should_hold_for_review is True


def test_value_above_contract_is_high_risk_and_reports_value_saved():
    appointment = _make_appointment()
    contract_item = _make_contract_item(agreed_price=150.00)

    result = assess(appointment, contract_item, charged_value=180.00)

    assert result.level == "high"
    assert "value_above_contract" in result.reasons
    assert result.value_saved_by_correction == 30  # 180 - 150 evitado de ser glosado


def test_value_below_contract_is_medium_risk_without_value_saved():
    appointment = _make_appointment()
    contract_item = _make_contract_item(agreed_price=150.00)

    result = assess(appointment, contract_item, charged_value=120.00)

    assert result.level == "medium"
    assert "value_below_contract_revenue_leak" in result.reasons
    assert result.value_saved_by_correction == 0
    assert result.should_hold_for_review is False


def test_missing_contract_reference_is_medium_risk():
    appointment = _make_appointment()

    result = assess(appointment, contract_item=None, charged_value=150.00)

    assert result.level == "medium"
    assert "no_contract_reference" in result.reasons


def test_clean_billing_is_low_risk():
    appointment = _make_appointment()
    contract_item = _make_contract_item(agreed_price=150.00)

    result = assess(appointment, contract_item, charged_value=150.00)

    assert result.level == "low"
    assert result.reasons == []
    assert result.should_hold_for_review is False
