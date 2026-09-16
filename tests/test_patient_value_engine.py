"""tests/test_patient_value_engine.py — testes puros (sem banco), mesmo
estilo de test_no_show_risk_engine.py e test_denial_risk_engine.py.
"Equilíbrio Insighta" (Balanced Scorecard, perna Cliente, mecanismo 1)."""
from app.services.patient_value_engine import VIP_MIN_REFERRALS, VIP_MIN_VISITS, compute_vip_status


def test_below_both_thresholds_is_not_vip():
    status = compute_vip_status(visit_count=1, referral_count=0)
    assert status.is_vip is False
    assert status.reasons == []


def test_frequent_patient_alone_is_vip():
    status = compute_vip_status(visit_count=VIP_MIN_VISITS, referral_count=0)
    assert status.is_vip is True
    assert status.reasons == ["frequente"]


def test_referring_patient_alone_is_vip_even_with_little_history():
    # Paciente novo que já trouxe outros não precisa esperar acumular
    # visitas próprias pra já ser valioso pra clínica.
    status = compute_vip_status(visit_count=1, referral_count=VIP_MIN_REFERRALS)
    assert status.is_vip is True
    assert status.reasons == ["indicou outros pacientes"]


def test_both_signals_present_lists_both_reasons():
    status = compute_vip_status(visit_count=VIP_MIN_VISITS, referral_count=VIP_MIN_REFERRALS)
    assert status.is_vip is True
    assert status.reasons == ["frequente", "indicou outros pacientes"]


def test_just_below_visit_threshold_is_not_vip_by_frequency_alone():
    status = compute_vip_status(visit_count=VIP_MIN_VISITS - 1, referral_count=0)
    assert status.is_vip is False
