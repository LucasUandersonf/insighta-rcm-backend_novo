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
    raw = _csv("12345678909;Maria Silva;Unimed Nacional;10101012;J06;1.234,56;15/01/2026")
    results = parse(raw)
    assert len(results) == 1
    assert results[0].row is not None
    assert results[0].row.charged_value == pytest.approx(1234.56)


def test_parse_csv_with_dot_decimal_values_does_not_inflate() -> None:
    """Reprodução direta do incidente: arquivo de origem em ponto-decimal
    simples (sem separador de milhar) — o valor final tem que continuar
    sendo o valor real, não 100x maior."""
    raw = _csv("12345678909;Maria Silva;Unimed Nacional;10101012;J06;1234.56;15/01/2026")
    results = parse(raw)
    assert len(results) == 1
    assert results[0].row is not None
    assert results[0].row.charged_value == pytest.approx(1234.56)
    assert results[0].row.charged_value != pytest.approx(123456.0)


def test_parse_csv_mixed_batch_both_formats_in_same_file() -> None:
    """Um arquivo pode ter linhas de fontes/exportações diferentes — cada
    linha é normalizada independentemente, sem estado compartilhado."""
    raw = _csv(
        "11122233981;Paciente BR;Amil;10101012;J06;1.234,56;15/01/2026",
        "22233344073;Paciente Dot;Amil;10101012;J06;1234.56;16/01/2026",
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
    raw = _csv("12345678909;Maria Silva;Unimed Nacional;10101012;J06;999999999,00;15/01/2026")
    results = parse(raw)
    assert len(results) == 1
    assert results[0].row is None
    assert results[0].errors is not None


# ---------------------------------------------------------------------
# Escopo completo de pessoa física (pedido do usuário: "todo sistema tem
# dados de pessoa física com nome, telefone, data de nascimento,
# endereço, email, CPF, sexo") — ver DECISÃO completa em
# app/sql/058_patient_full_identity.sql e RawBillingRow (app/worker/schemas.py).
# ---------------------------------------------------------------------
_EXTENDED_IDENTITY_HEADER = (
    "cpf_paciente;nome_paciente;convenio;codigo_procedimento;cid;valor_cobrado;data_atendimento;"
    "telefone_paciente;email_paciente;data_nascimento_paciente;sexo_paciente;"
    "endereco_paciente;cidade_paciente;uf_paciente;cep_paciente"
)


def _csv_extended(*rows: str) -> bytes:
    return "\n".join([_EXTENDED_IDENTITY_HEADER, *rows]).encode("utf-8-sig")


def test_parse_csv_captures_full_patient_identity_fields() -> None:
    raw = _csv_extended(
        "12345678909;Maria Silva;Unimed Nacional;10101012;J06;150,00;15/01/2026;"
        "(11) 98888-7777;maria@example.com;05/03/1990;Feminino;"
        "Rua das Flores, 123;São Paulo;sp;01310-100"
    )
    results = parse(raw)
    assert len(results) == 1
    row = results[0].row
    assert row is not None
    assert row.patient_phone == "11988887777"
    assert row.patient_email == "maria@example.com"
    assert row.patient_birth_date.isoformat() == "1990-03-05"
    assert row.patient_sex == "F"
    assert row.patient_address_street == "Rua das Flores, 123"
    assert row.patient_address_city == "São Paulo"
    assert row.patient_address_state == "SP"
    assert row.patient_zip_code == "01310100"


def test_parse_csv_without_extended_identity_columns_still_works() -> None:
    """Mesmo critério dos demais campos opcionais do template: um export
    que não tem essas colunas continua funcionando exatamente como antes."""
    raw = _csv("12345678909;Maria Silva;Unimed Nacional;10101012;J06;150,00;15/01/2026")
    results = parse(raw)
    assert len(results) == 1
    row = results[0].row
    assert row is not None
    assert row.patient_phone is None
    assert row.patient_email is None
    assert row.patient_birth_date is None
    assert row.patient_sex is None


def test_parse_csv_rejects_cpf_with_wrong_check_digit() -> None:
    """CPF é a chave de deduplicação de paciente — um valor
    estruturalmente inválido (dígito verificador errado) rejeita a
    linha, nunca vira identidade de paciente silenciosamente."""
    raw = _csv("12345678900;Maria Silva;Unimed Nacional;10101012;J06;150,00;15/01/2026")
    results = parse(raw)
    assert len(results) == 1
    assert results[0].row is None
    assert "CPF inválido" in results[0].errors[0]


def test_parse_csv_rejects_repeated_digit_cpf() -> None:
    raw = _csv("11111111111;Maria Silva;Unimed Nacional;10101012;J06;150,00;15/01/2026")
    results = parse(raw)
    assert len(results) == 1
    assert results[0].row is None


def test_parse_csv_rejects_invalid_email() -> None:
    raw = _csv_extended(
        "12345678909;Maria Silva;Unimed Nacional;10101012;J06;150,00;15/01/2026;;"
        "nao-e-email;;;;;;"
    )
    results = parse(raw)
    assert len(results) == 1
    assert results[0].row is None
    assert "E-mail" in results[0].errors[0]


def test_parse_csv_rejects_unrecognized_sex_value() -> None:
    raw = _csv_extended(
        "12345678909;Maria Silva;Unimed Nacional;10101012;J06;150,00;15/01/2026;;;;"
        "Indefinido;;;;"
    )
    results = parse(raw)
    assert len(results) == 1
    assert results[0].row is None
    assert "sexo" in results[0].errors[0]
