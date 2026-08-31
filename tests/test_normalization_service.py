"""
tests/test_normalization_service.py

Testa só a função pura slugify — a parte de normalize_row() que depende
de sessão de banco fica coberta por testes de integração (fora do
escopo deste skeleton, ver README). O objetivo aqui é documentar o
comportamento esperado do slug com casos reais de convênio.

CORREÇÃO: este arquivo importava `_slugify` de
app.services.normalization_service, função que não existe mais ali —
foi extraída para app.core.text_utils.slugify (pública) em algum
refactor anterior, e o teste ficou órfão apontando para o módulo
errado. Isso quebrava a COLETA do pytest (ImportError), não só o
teste em si — nenhum teste deste arquivo rodava. Corrigido para
importar do local real.
"""
from app.core.text_utils import slugify


def test_slugify_removes_accents_and_punctuation():
    assert slugify("UNIMED NAC.") == "unimed_nac"


def test_slugify_collapses_multiple_separators():
    assert slugify("Bradesco  Saúde  -  Nacional") == "bradesco_saude_nacional"


def test_slugify_is_case_insensitive():
    assert slugify("unimed nacional") == slugify("UNIMED NACIONAL")


def test_slugify_strips_leading_trailing_junk():
    assert slugify("  -- SulAmérica --  ") == "sulamerica"
