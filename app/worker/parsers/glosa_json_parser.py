"""
app/worker/parsers/glosa_json_parser.py

Formato JSON do Template de Integração "Glosa" — mesmo espírito de
agenda_json_parser.py/json_parser.py: array de objetos usando as MESMAS
chaves snake_case do template CSV. `data_pagamento` já é uma data ISO
completa; pydantic converte sozinho.
"""
import json

from pydantic import ValidationError

from app.worker.schemas import DenialRowParseResult, RawDenialRow


def parse(raw_bytes: bytes) -> list[DenialRowParseResult]:
    try:
        data = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        return [DenialRowParseResult.failed(row_number=0, exc=exc)]

    if not isinstance(data, list):
        return [DenialRowParseResult.failed(row_number=0, exc=ValueError("JSON raiz deve ser uma lista de pagamentos."))]

    results: list[DenialRowParseResult] = []
    for row_number, item in enumerate(data, start=1):
        try:
            mapped = {
                "insurance_plan_raw_name": item.get("convenio", ""),
                "guia_numero": item.get("guia_numero") or None,
                # Chave de conciliação alternativa (achado do Dicionário
                # de Dados) — ver DECISÃO em RawDenialRow.
                "numero_carteirinha": item.get("numero_carteirinha") or None,
                # Achado 6 da Auditoria (médio) — confirmação cruzada de
                # identidade, opcional (ver DECISÃO em RawDenialRow.patient_cpf).
                "patient_cpf": item.get("cpf_beneficiario") or None,
                "procedure_code": item.get("codigo_procedimento") or None,
                "received_value": item.get("valor_pago"),
                "settlement_date": item.get("data_pagamento"),  # pydantic converte "aaaa-mm-dd" sozinho
                "codigo_motivo": item.get("codigo_motivo_glosa") or None,
                "descricao_motivo": item.get("descricao_motivo_glosa") or None,
            }
            row = RawDenialRow.model_validate(mapped)
            results.append(DenialRowParseResult.ok(row_number, row))
        except (ValidationError, TypeError) as exc:
            results.append(DenialRowParseResult.failed(row_number, exc))
    return results
