"""
tests/test_column_mapping_service.py

Função pura (sem banco) — cobre o Mapeador Automático de Coluna (ver
DECISÃO em app/sql/021_ingestion_column_aliases.sql e docstring de
app/services/column_mapping_service.py).
"""
import pytest

from app.services.column_mapping_service import (
    REQUIRED_CANONICAL_FIELDS,
    EmptyCsvHeaderError,
    extract_csv_headers,
    suggest_mapping,
)


def test_all_standard_headers_need_no_suggestion():
    headers = ["cpf_paciente", "nome_paciente", "convenio", "codigo_procedimento", "valor_cobrado", "data_atendimento"]

    preview = suggest_mapping(headers, existing_aliases={})

    assert preview.suggested_mapping == {}
    assert preview.unresolved_required_fields == []


def test_case_variant_header_is_matched_via_label():
    """"CONVENIO" (maiúsculo) não bate exatamente com a chave "convenio"
    do dicionário padrão (comparação é case-sensitive), mas a
    similaridade com o RÓTULO "Convênio" deve resolver isso sozinha."""
    headers = ["cpf_paciente", "nome_paciente", "CONVENIO", "codigo_procedimento", "valor_cobrado", "data_atendimento"]

    preview = suggest_mapping(headers, existing_aliases={})

    assert preview.suggested_mapping.get("CONVENIO") == "insurance_plan_raw_name"
    assert "insurance_plan_raw_name" not in preview.unresolved_required_fields


def test_unrecognizable_header_stays_unresolved_rather_than_guessed():
    """Um cabeçalho sem nenhuma semelhança plausível (ex: uma coluna
    totalmente fora do domínio) não deve virar um "chute" — melhor ficar
    pendente de mapear manualmente."""
    headers = ["col_aleatoria_sem_sentido", "outra_coluna_qualquer"]

    preview = suggest_mapping(headers, existing_aliases={})

    assert preview.suggested_mapping == {}
    assert set(preview.unresolved_required_fields) == set(REQUIRED_CANONICAL_FIELDS)


def test_existing_alias_resolves_a_field_without_new_suggestion():
    headers = ["cpf_paciente", "nome_paciente", "NOME_CONVENIO", "codigo_procedimento", "valor_cobrado", "data_atendimento"]

    preview = suggest_mapping(headers, existing_aliases={"NOME_CONVENIO": "insurance_plan_raw_name"})

    # Já resolvido por alias existente — não deveria aparecer como uma
    # "nova" sugestão nem como pendente.
    assert "NOME_CONVENIO" not in preview.suggested_mapping
    assert "insurance_plan_raw_name" not in preview.unresolved_required_fields


def test_two_similar_headers_do_not_both_claim_the_same_field():
    """Só um cabeçalho pode ser sugerido por campo canônico — o de maior
    similaridade ganha, o outro fica disponível para outro campo (ou
    pendente, se não servir para nada)."""
    headers = ["nome_paciente_completo", "nome_paciente", "convenio", "codigo_procedimento", "valor_cobrado", "data_atendimento"]

    preview = suggest_mapping(headers, existing_aliases={})

    # "nome_paciente" já é um cabeçalho PADRÃO (bate direto) — só
    # "nome_paciente_completo" sobra como candidato a sugestão, e não há
    # mais nenhum campo obrigatório sem cabeçalho reconhecido.
    assert "patient_name" not in preview.unresolved_required_fields


def test_extract_csv_headers_reads_only_the_first_line():
    csv_bytes = "cpf_paciente;nome_paciente\r\n12345678900;Fulano\r\n".encode("utf-8-sig")

    headers = extract_csv_headers(csv_bytes)

    assert headers == ["cpf_paciente", "nome_paciente"]


def test_extract_csv_headers_rejects_empty_file():
    with pytest.raises(EmptyCsvHeaderError):
        extract_csv_headers(b"")
