"""
app/worker/parsers/glosa_csv_parser.py

Parser do Template de Integração "Glosa" (demonstrativo de pagamento) —
ver docstring de RawDenialRow (app/worker/schemas.py). Mesmo estilo de
csv_parser.py/agenda_csv_parser.py (stdlib csv.DictReader, ';' como
separador, utf-8-sig tolera BOM de export Windows).
"""
import csv
import io
from datetime import date, datetime

from pydantic import ValidationError

from app.worker.schemas import DenialRowParseResult, RawDenialRow

_EXPECTED_HEADERS = {
    "convenio": "insurance_plan_raw_name",
    "guia_numero": "guia_numero",
    "codigo_procedimento": "procedure_code",
    "valor_pago": "received_value",
    "data_pagamento": "settlement_date",
    "codigo_motivo_glosa": "codigo_motivo",
    "descricao_motivo_glosa": "descricao_motivo",
}

_OPTIONAL_STRING_FIELDS = ("procedure_code", "codigo_motivo", "descricao_motivo")


def parse(raw_bytes: bytes) -> list[DenialRowParseResult]:
    text = raw_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")

    results: list[DenialRowParseResult] = []
    for row_number, raw_row in enumerate(reader, start=1):
        try:
            mapped = {
                canonical_field: raw_row.get(csv_header, "").strip()
                for csv_header, canonical_field in _EXPECTED_HEADERS.items()
            }
            for field in _OPTIONAL_STRING_FIELDS:
                mapped[field] = mapped[field] or None
            mapped["received_value"] = _normalize_value(mapped["received_value"])
            settlement_raw = raw_row.get("data_pagamento", "").strip()
            mapped["settlement_date"] = _parse_br_date(settlement_raw) if settlement_raw else None

            row = RawDenialRow.model_validate(mapped)
            results.append(DenialRowParseResult.ok(row_number, row))
        except (ValidationError, ValueError) as exc:
            results.append(DenialRowParseResult.failed(row_number, exc))
    return results


def _normalize_value(raw: str) -> str:
    """Mesma tolerância de formato BR ("1.234,56") vs decimal simples
    ("1234.56") que csv_parser._normalize_charged_value já resolve —
    reaproveitada aqui em vez de duplicar a heurística, já que a mesma
    ambiguidade de separador existe para qualquer valor monetário vindo
    de um export de ERP, não só valor_cobrado."""
    from app.worker.parsers.csv_parser import _normalize_charged_value

    if not raw:
        raise ValueError("valor_pago vazio")
    return _normalize_charged_value(raw)


def _parse_br_date(value: str) -> date:
    return datetime.strptime(value, "%d/%m/%Y").date()
