"""
tests/test_csv_parser.py

Teste de regressão do achado F-01 da Auditoria Go-Live: o parser CSV de
ingestão corrompia silenciosamente valores monetários que não vinham em
formato BR estrito (separador de milhar "." + decimal ","). Este arquivo
existe especificamente para que esse bug NUNCA mais volte sem quebrar a
suíte — cobre tanto a função pura de normalização quanto o parser
completo ponta a ponta com um CSV simulado.
"""
import pytest

from app.worker.parsers.csv_parser import AmbiguousCurrencyFormatError, _normalize_charged_value, parse

# ---------------------------------------------------------------------
# _normalize_charged_value — casos determinísticos (os dois separadores
# presentes na mesma string, nenhuma ambiguidade possível)
# ---------------------------------------------------------------------
DETERMINISTIC_CASES = [
    ("1.234,56", "1234.56"),   # BR com milhar: o bug original processava certo
    ("1,234.56", "1234.56"),   # internacional com milhar
    ("12.345.678,90", "12345678.90"),  # BR com múltiplos separadores de milhar
]

# ---------------------------------------------------------------------
# _normalize_charged_value — um único tipo de separador (heurística por
# contagem de dígitos). Estes são os casos que o bug original (F-01)
# corrompia sempre que o separador único era "." em vez de ",".
# ---------------------------------------------------------------------
HEURISTIC_CASES = [
    ("150,00", "150.00"),      # BR simples — já funcionava antes
    ("150.00", "150.00"),      # ponto-decimal simples — ANTES virava "15000" (bug)
    ("1234.56", "1234.56"),    # ponto-decimal com centavos — ANTES virava "123456" (bug)
    ("99.90", "99.90"),        # ANTES virava "9990" (bug)
    ("1.500", "1500"),         # milhar sem centavos (3 dígitos após o separador) -> R$1.500
    ("1,500", "1500"),         # mesma heurística, separador vírgula
    ("150", "150"),            # sem separador algum: valor inteiro em reais
]


@pytest.mark.parametrize("raw,expected", DETERMINISTIC_CASES + HEURISTIC_CASES)
def test_normalize_charged_value(raw: str, expected: str) -> None:
    assert _normalize_charged_value(raw) == expected


def test_normalize_charged_value_rejects_empty_string() -> None:
    with pytest.raises(AmbiguousCurrencyFormatError):
        _normalize_charged_value("")


def test_no_100x_inflation_regression() -> None:
    """
    O teste mais importante deste arquivo: reproduz literalmente o
    cenário do achado F-01 e garante, em valor float final (não só na
    string intermediária), que um CSV com valor em ponto-decimal simples
    nunca mais é interpretado como 100x maior.
    """
    assert float(_normalize_charged_value("1234.56")) == pytest.approx(1234.56)
    assert float(_normalize_charged_value("1234.56")) != pytest.approx(123456.0)


# ---------------------------------------------------------------------
# parse() ponta a ponta — CSV completo, formato de header real
# (';' como delimitador, mesmo layout de _EXPECTED_HEADERS)
# ---------------------------------------------------------------------
_HEADER = "cpf_paciente;nome_paciente;convenio;codigo_procedimento;cid;valor_cobrado;data_atendimento"


def _csv(*rows: str) -> bytes:
    return "\n".join([_HEADER, *rows]).encode("utf-8-sig")


def test_parse_csv_with_br_format_values() -> None:
    raw = _csv("12345678900;Maria Silva;Unimed Nacional;10101012;J06;1.234,56;15/01/2026")
    results = parse(raw)
    assert len(results) == 1
    assert results[0].row is not None
    assert results[0].row.charged_value == pytest.approx(1234.56)


def test_parse_csv_with_dot_decimal_values_does_not_inflate() -> None:
    """Reprodução direta do incidente: arquivo de origem em ponto-decimal
    simples (sem separador de milhar) — o valor final tem que continuar
    sendo o valor real, não 100x maior."""
    raw = _csv("12345678900;Maria Silva;Unimed Nacional;10101012;J06;1234.56;15/01/2026")
    results = parse(raw)
    assert len(results) == 1
    assert results[0].row is not None
    assert results[0].row.charged_value == pytest.approx(1234.56)
    assert results[0].row.charged_value != pytest.approx(123456.0)


def test_parse_csv_mixed_batch_both_formats_in_same_file() -> None:
    """Um arquivo pode ter linhas de fontes/exportações diferentes — cada
    linha é normalizada independentemente, sem estado compartilhado."""
    raw = _csv(
        "11111111111;Paciente BR;Amil;10101012;J06;1.234,56;15/01/2026",
        "22222222222;Paciente Dot;Amil;10101012;J06;1234.56;16/01/2026",
    )
    results = parse(raw)
    assert len(results) == 2
    assert results[0].row.charged_value == pytest.approx(1234.56)
    assert results[1].row.charged_value == pytest.approx(1234.56)


def test_parse_csv_grossly_inflated_value_is_rejected_by_sanity_cap() -> None:
    """Rede de segurança adicional (schemas.py, Field(le=500_000)): mesmo
    que uma futura regressão reintroduza o bug de inflação, um valor
    absurdo para um procedimento único vira linha `failed`, nunca dado
    silenciosamente errado em billing."""
    raw = _csv("12345678900;Maria Silva;Unimed Nacional;10101012;J06;999999999,00;15/01/2026")
    results = parse(raw)
    assert len(results) == 1
    assert results[0].row is None
    assert results[0].errors is not None
