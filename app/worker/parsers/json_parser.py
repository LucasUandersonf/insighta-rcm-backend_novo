"""
app/worker/parsers/json_parser.py

Formato esperado: um array JSON de objetos já bem próximos do schema
canônico — é o formato mais comum vindo de integrações modernas (Etapa 1
do briefing menciona "API de integração com sistemas mais modernos").
"""
import json
from datetime import date

from pydantic import ValidationError

from app.worker.schemas import RawBillingRow, RowParseResult


def parse(raw_bytes: bytes) -> list[RowParseResult]:
    try:
        data = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        # Arquivo inteiro ilegível: uma única "linha" de erro representando
        # o arquivo todo, para não confundir com erro de uma linha específica.
        return [RowParseResult.failed(row_number=0, exc=exc)]

    if not isinstance(data, list):
        return [RowParseResult.failed(row_number=0, exc=ValueError("JSON raiz deve ser uma lista de atendimentos."))]

    results: list[RowParseResult] = []
    for row_number, item in enumerate(data, start=1):
        try:
            mapped = {
                "patient_cpf": item.get("cpf_paciente"),
                "patient_name": item.get("nome_paciente", ""),
                # Opcional (achado F-02, Auditoria Go-Live) — ver docstring
                # de RawBillingRow em app/worker/schemas.py.
                "professional_name": item.get("nome_profissional") or None,
                "professional_registry": item.get("registro_profissional") or None,
                "insurance_plan_raw_name": item.get("convenio", ""),
                "procedure_code": item.get("codigo_procedimento", ""),
                "cid_code": item.get("cid"),
                "charged_value": item.get("valor_cobrado"),
                "service_date": item.get("data_atendimento"),  # pydantic já converte "aaaa-mm-dd" em date
            }
            row = RawBillingRow.model_validate(mapped)
            results.append(RowParseResult.ok(row_number, row))
        except (ValidationError, TypeError) as exc:
            results.append(RowParseResult.failed(row_number, exc))
    return results
