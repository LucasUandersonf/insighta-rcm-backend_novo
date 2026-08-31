"""
app/worker/parsers/csv_parser.py

DECISÃO — csv.DictReader (stdlib) em vez de pandas para o parsing
-------------------------------------------------------------------------
Para o volume esperado de um arquivo diário de uma clínica (milhares de
linhas, não milhões), csv.DictReader é suficiente e evita puxar pandas
como dependência pesada só para ler um CSV linha a linha — importante
num worker que deve ter footprint de memória pequeno e previsível
rodando continuamente em produção.

CORREÇÃO CRÍTICA (Auditoria Go-Live, achado F-01) — normalização de
valor monetário deixou de assumir formato BR cegamente
-------------------------------------------------------------------------
A versão anterior fazia `.replace(".", "").replace(",", ".")`
incondicionalmente, assumindo que TODO arquivo de origem usa separador
de milhar "." e decimal ",". Isso corrompe silenciosamente qualquer
arquivo que já venha em ponto decimal simples: "1234.56" virava "123456"
(100x maior) e passava por toda a validação sem erro, porque
`charged_value: float = Field(gt=0)` só exige um número positivo — o
bug nunca aparecia como erro estrutural, só como dado financeiro errado
nos dashboards e no motor de glosa.

A correção usa uma regra determinística e sem ambiguidade sempre que os
DOIS separadores aparecem juntos, e uma heurística de contagem de
dígitos (padrão usado por parsers de moeda em produção) quando só um
tipo de separador aparece — ver `_normalize_charged_value` e os casos
cobertos em tests/test_csv_parser.py.
"""
import csv
import io
from datetime import date, datetime

from pydantic import ValidationError

from app.worker.schemas import RawBillingRow, RowParseResult

# Mapeamento de cabeçalho esperado do CSV -> campo do schema canônico.
# Centralizado aqui para o dia em que precisarmos suportar um layout de
# CSV ligeiramente diferente por tenant (ficaria um dict por tenant, não
# uma reescrita do parser).
_EXPECTED_HEADERS = {
    "cpf_paciente": "patient_cpf",
    "nome_paciente": "patient_name",
    # Colunas OPCIONAIS (achado F-02 da Auditoria Go-Live): quando o
    # export do cliente não tem essas colunas, .get(..., "") devolve
    # string vazia -> RawBillingRow trata como None (ver mapped abaixo).
    "nome_profissional": "professional_name",
    "registro_profissional": "professional_registry",
    "convenio": "insurance_plan_raw_name",
    "codigo_procedimento": "procedure_code",
    "cid": "cid_code",
    "valor_cobrado": "charged_value",
    "data_atendimento": "service_date",
}


def parse(raw_bytes: bytes) -> list[RowParseResult]:
    text = raw_bytes.decode("utf-8-sig")  # utf-8-sig tolera BOM comum em export de sistema legado Windows
    reader = csv.DictReader(io.StringIO(text), delimiter=";")  # ';' é o separador mais comum em export BR (decimal usa vírgula)

    results: list[RowParseResult] = []
    for row_number, raw_row in enumerate(reader, start=1):
        try:
            mapped = {
                canonical_field: raw_row.get(csv_header, "").strip()
                for csv_header, canonical_field in _EXPECTED_HEADERS.items()
            }
            # Campos opcionais: string vazia (coluna ausente ou célula em
            # branco) vira None, não "" — RawBillingRow.professional_name
            # é str | None, e "" não deve contar como "profissional com
            # nome vazio" na normalização (get_or_create trataria isso
            # como um profissional real chamado "").
            mapped["professional_name"] = mapped["professional_name"] or None
            mapped["professional_registry"] = mapped["professional_registry"] or None
            mapped["charged_value"] = _normalize_charged_value(mapped["charged_value"])
            mapped["service_date"] = _parse_br_date(mapped["service_date"])

            row = RawBillingRow.model_validate(mapped)
            results.append(RowParseResult.ok(row_number, row))
        except (ValidationError, ValueError) as exc:
            results.append(RowParseResult.failed(row_number, exc))
    return results


class AmbiguousCurrencyFormatError(ValueError):
    """Levantado quando a string de valor não tem nenhum dígito
    reconhecível — nunca "adivinhada" como zero ou descartada em
    silêncio. Vira uma linha `failed` normal (ver RowParseResult),
    igual a qualquer outro erro de validação estrutural."""


def _normalize_charged_value(raw: str) -> str:
    """
    Converte a string de valor monetário do CSV (em QUALQUER formato de
    origem plausível) para o formato canônico com ponto decimal que
    `RawBillingRow.charged_value: float` espera.

    REGRA — determinística quando os dois separadores aparecem juntos
    -------------------------------------------------------------------
    Se "." e "," aparecem na mesma string, o ÚLTIMO (mais à direita) é,
    por definição, o separador DECIMAL — é assim que toda notação
    numérica real funciona, seja BR ("1.234,56") ou internacional
    ("1,234.56"): o separador de milhar nunca vem depois do decimal.
    Todas as ocorrências do OUTRO caractere são removidas (milhar).

    REGRA — heurística quando só um tipo de separador aparece
    -------------------------------------------------------------------
    Com um único "." ou "," na string, a posição sozinha é ambígua
    ("1.234" pode ser mil e duzentos e trinta e quatro reais OU um
    reais e 234 milésimos, que não existe em moeda). Resolvemos pela
    contagem de dígitos após o ÚLTIMO separador — o mesmo heurístico
    usado por parsers de moeda de produção:
      - exatamente 2 dígitos depois  -> é decimal (padrão de centavos:
        "150.00", "150,00").
      - exatamente 3 dígitos depois  -> é separador de milhar sem
        centavos ("1.500" = R$1.500, "1,500" = R$1.500) — removido,
        sem ponto decimal.
      - qualquer outra contagem (0, 1, 4+) -> tratado como decimal, por
        ser o caso mais comum e o que causa menor dano se a heurística
        errar (nunca infla o valor por um fator de milhar).
    Sem NENHUM separador, a string já é o valor inteiro em reais.
    """
    value = raw.strip()
    if not value:
        raise AmbiguousCurrencyFormatError("valor_cobrado vazio")

    has_dot = "." in value
    has_comma = "," in value

    if has_dot and has_comma:
        decimal_sep = "," if value.rfind(",") > value.rfind(".") else "."
        thousands_sep = "." if decimal_sep == "," else ","
        value = value.replace(thousands_sep, "")
        if decimal_sep == ",":
            value = value.replace(",", ".")
        return value

    sep = "." if has_dot else ("," if has_comma else None)
    if sep is None:
        return value  # sem separador algum: já é um valor inteiro em reais

    digits_after = len(value) - value.rfind(sep) - 1
    if digits_after == 3:
        return value.replace(sep, "")  # separador de milhar, sem centavos
    return value.replace(sep, ".")  # tratado como decimal (2 dígitos é o caso comum; demais casos, ver docstring)


def _parse_br_date(value: str) -> date:
    # Sistemas legados de clínica costumam exportar dd/mm/aaaa.
    return datetime.strptime(value, "%d/%m/%Y").date()
