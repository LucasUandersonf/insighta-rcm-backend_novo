"""
tests/test_denial_appeal_draft_service.py

Mesmo princípio de test_contract_extraction_service.py: a parte que
decide "o que vai no prompt" (`build_draft_user_message`) é uma função
PURA, testável sem rede/ANTHROPIC_API_KEY.
"""
from app.services.denial_appeal_draft_service import build_draft_user_message


def _base_context(**overrides) -> dict:
    context = {
        "appeal_type": "administrativa",
        "operator_denial_reason": "Falta de guia de autorização prévia.",
        "insurance_plan_name": "Unimed Nacional",
        "procedure_code": "10101012",
        "cid_code": "M54.5",
        "guia_tipo": "consulta",
        "guia_numero": "998877",
        "guia_senha": "AUTH123",
        "charged_value": 250.0,
    }
    context.update(overrides)
    return context


def test_draft_message_includes_all_provided_facts():
    message = build_draft_user_message(_base_context())
    assert "recurso administrativo" in message.lower()
    assert "Unimed Nacional" in message
    assert "Falta de guia de autorização prévia." in message
    assert "10101012" in message
    assert "M54.5" in message
    assert "998877" in message
    assert "AUTH123" in message
    assert "R$ 250,00" in message


def test_draft_message_never_invents_missing_facts():
    """Sem guia vinculada (billing.guia_id nulo, cenário real e comum —
    ver DECISÃO em app/sql/015_billing_guia.sql): a mensagem precisa
    dizer explicitamente que NÃO há guia, nunca inventar um número."""
    context = _base_context(guia_tipo=None, guia_numero=None, guia_senha=None)
    message = build_draft_user_message(context)
    assert "não há guia cadastrada" in message.lower()


def test_draft_message_handles_missing_operator_reason_and_optional_fields():
    context = _base_context(operator_denial_reason=None, procedure_code=None, cid_code=None, charged_value=None)
    message = build_draft_user_message(context)
    assert "não informado" in message.lower()
    # Sem valor cobrado, a linha de valor não deveria aparecer forçada.
    assert "R$" not in message


def test_draft_message_labels_appeal_type_in_portuguese_for_each_type():
    for appeal_type, expected_fragment in [
        ("tecnica", "técnico"),
        ("administrativa", "administrativo"),
        ("medica", "médico"),
    ]:
        message = build_draft_user_message(_base_context(appeal_type=appeal_type))
        assert expected_fragment in message.lower()
