"""
app/worker/parsers/glosa_xml_parser.py

Formato XML do Template de Integração "Glosa" — mesma decisão de
segurança de xml_parser.py (defusedxml) e mesmo padrão camelCase de tag
dos demais parsers XML. `<pagamento>` é o elemento repetido (uma linha do
demonstrativo da operadora); `dataPagamento` é uma data ISO
(`aaaa-mm-dd`), não formato BR.
"""
from datetime import date, datetime

from defusedxml import ElementTree as ET
from pydantic import ValidationError

from app.worker.schemas import DenialRowParseResult, RawDenialRow


def parse(raw_bytes: bytes) -> list[DenialRowParseResult]:
    root = ET.fromstring(raw_bytes)

    results: list[DenialRowParseResult] = []
    for row_number, pagamento in enumerate(root.findall(".//pagamento"), start=1):
        try:
            settlement_raw = _text(pagamento, "dataPagamento")
            mapped = {
                "insurance_plan_raw_name": _text(pagamento, "convenio"),
                "guia_numero": _text(pagamento, "guiaNumero") or None,
                # Chave de conciliação alternativa (achado do Dicionário
                # de Dados) — ver DECISÃO em RawDenialRow.
                "numero_carteirinha": _text(pagamento, "numeroCarteirinha") or None,
                "procedure_code": _text(pagamento, "codigoProcedimento") or None,
                "received_value": _text(pagamento, "valorPago").replace(",", "."),
                "settlement_date": _parse_iso_date(settlement_raw) if settlement_raw else None,
                "codigo_motivo": _text(pagamento, "codigoMotivoGlosa") or None,
                "descricao_motivo": _text(pagamento, "descricaoMotivoGlosa") or None,
            }
            row = RawDenialRow.model_validate(mapped)
            results.append(DenialRowParseResult.ok(row_number, row))
        except (ValidationError, ValueError, AttributeError) as exc:
            results.append(DenialRowParseResult.failed(row_number, exc))
    return results


def _text(element, tag: str) -> str:
    node = element.find(tag)
    return (node.text or "").strip() if node is not None else ""


def _parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()
