"""
tests/test_contract_extraction_service.py

`validate_extracted_items` é PURA (não toca rede nem banco) — mesmo
princípio de test_denial_risk_engine.py e test_smart_insights_engine.py.
Só este arquivo, entre os três que tocam o Parser Inteligente de
Contratos, roda sem sqlalchemy nem httpx configurados de verdade —
propositalmente, para a lógica de confiança da extração ser testável
isoladamente da chamada à IA.
"""
from app.services.contract_extraction_service import ExtractedItem, detect_price_anomalies, validate_extracted_items


def test_valid_items_pass_through_unchanged():
    result = validate_extracted_items(
        [
            {"tuss_code": "10101012", "procedure_name": "Consulta em consultório", "agreed_price": 150.00},
            {"tuss_code": "20202020", "procedure_name": "Exame X", "agreed_price": 89.90},
        ]
    )
    assert len(result.items) == 2
    assert result.items[0].tuss_code == "10101012"
    assert result.items[0].warning is None
    assert result.warnings == []


def test_punctuation_in_tuss_code_is_sanitized():
    result = validate_extracted_items([{"tuss_code": "101.01.012", "agreed_price": 150.0}])
    assert len(result.items) == 1
    assert result.items[0].tuss_code == "10101012"


def test_comma_decimal_price_is_parsed():
    result = validate_extracted_items([{"tuss_code": "10101012", "agreed_price": "150,00"}])
    assert result.items[0].agreed_price == 150.00


def test_row_without_tuss_code_is_discarded_with_warning():
    result = validate_extracted_items([{"tuss_code": "", "agreed_price": 150.0}])
    assert result.items == []
    assert any("código TUSS identificável" in w for w in result.warnings)


def test_row_with_invalid_price_is_discarded_with_warning():
    result = validate_extracted_items([{"tuss_code": "10101012", "agreed_price": "não é número"}])
    assert result.items == []
    assert any("preço inválido" in w for w in result.warnings)


def test_zero_or_negative_price_is_discarded():
    result = validate_extracted_items([{"tuss_code": "10101012", "agreed_price": 0}, {"tuss_code": "20202020", "agreed_price": -10}])
    assert result.items == []


def test_short_tuss_code_gets_a_confirmation_warning_but_is_not_discarded():
    result = validate_extracted_items([{"tuss_code": "123", "agreed_price": 150.0}])
    assert len(result.items) == 1
    assert result.items[0].warning is not None
    assert "fora do padrão" in result.items[0].warning


def test_duplicate_tuss_code_keeps_last_occurrence_and_warns():
    result = validate_extracted_items(
        [
            {"tuss_code": "10101012", "agreed_price": 100.0, "procedure_name": "Preço antigo"},
            {"tuss_code": "10101012", "agreed_price": 150.0, "procedure_name": "Preço reajustado"},
        ]
    )
    assert len(result.items) == 1
    assert result.items[0].agreed_price == 150.0
    assert result.items[0].procedure_name == "Preço reajustado"
    assert any("mais de uma vez" in w for w in result.warnings)


def test_empty_extraction_warns_that_nothing_was_extracted():
    result = validate_extracted_items([])
    assert result.items == []
    assert any("Nenhum item extraído" in w for w in result.warnings)


def test_missing_procedure_name_is_allowed():
    result = validate_extracted_items([{"tuss_code": "10101012", "agreed_price": 150.0}])
    assert result.items[0].procedure_name is None


# ---------------------------------------------------------------------
# detect_price_anomalies — mesma filosofia de função pura: nenhuma IA,
# só aritmética contra o dict de preços da tabela anterior.
# ---------------------------------------------------------------------


def test_price_at_or_above_2x_previous_is_flagged():
    items = [ExtractedItem(tuss_code="10101012", procedure_name="Consulta", agreed_price=620.0)]
    warnings = detect_price_anomalies(items, previous_prices={"10101012": 182.0})
    assert items[0].warning is not None
    assert "3,4x" in items[0].warning
    assert len(warnings) == 1
    assert "1 item tem" in warnings[0]


def test_price_below_2x_previous_is_not_flagged():
    items = [ExtractedItem(tuss_code="10101012", procedure_name="Consulta", agreed_price=200.0)]
    warnings = detect_price_anomalies(items, previous_prices={"10101012": 150.0})
    assert items[0].warning is None
    assert warnings == []


def test_item_without_previous_price_is_not_flagged():
    items = [ExtractedItem(tuss_code="99999999", procedure_name="Procedimento novo", agreed_price=1000.0)]
    warnings = detect_price_anomalies(items, previous_prices={})
    assert items[0].warning is None
    assert warnings == []


def test_anomaly_warning_is_appended_to_existing_format_warning():
    items = [
        ExtractedItem(
            tuss_code="123",
            procedure_name="Código curto",
            agreed_price=1000.0,
            warning="Código TUSS fora do padrão esperado (6 a 10 dígitos) — confira antes de homologar.",
        )
    ]
    detect_price_anomalies(items, previous_prices={"123": 100.0})
    assert "fora do padrão" in items[0].warning
    assert "10,0x" in items[0].warning


def test_plural_message_for_multiple_flagged_items():
    items = [
        ExtractedItem(tuss_code="10101012", procedure_name="A", agreed_price=400.0),
        ExtractedItem(tuss_code="20202020", procedure_name="B", agreed_price=500.0),
    ]
    warnings = detect_price_anomalies(items, previous_prices={"10101012": 100.0, "20202020": 100.0})
    assert len(warnings) == 1
    assert "2 itens têm" in warnings[0]
