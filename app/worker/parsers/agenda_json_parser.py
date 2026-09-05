"""
app/worker/parsers/agenda_json_parser.py

Formato JSON do Template de Integração "Agenda" — mesmo espírito de
json_parser.py (Faturamento): um array de objetos usando as MESMAS
chaves snake_case do template CSV (ver TEMPLATE_AGENDA.md), não os nomes
de campo internos de RawAppointmentRow. Diferente do CSV (data e hora em
colunas separadas), aqui `data_agendamento` já é um datetime ISO
completo (`aaaa-mm-ddThh:mm:ss` ou `aaaa-mm-dd`) — pydantic converte
sozinho, mesmo comportamento de `data_atendimento` no Faturamento.
"""
import json

from pydantic import ValidationError

from app.worker.schemas import AgendaRowParseResult, RawAppointmentRow


def parse(raw_bytes: bytes) -> list[AgendaRowParseResult]:
    try:
        data = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        return [AgendaRowParseResult.failed(row_number=0, exc=exc)]

    if not isinstance(data, list):
        return [AgendaRowParseResult.failed(row_number=0, exc=ValueError("JSON raiz deve ser uma lista de agendamentos."))]

    results: list[AgendaRowParseResult] = []
    for row_number, item in enumerate(data, start=1):
        try:
            mapped = {
                "patient_cpf": item.get("cpf_paciente"),
                "patient_name": item.get("nome_paciente", ""),
                "professional_name": item.get("nome_profissional") or None,
                "professional_registry": item.get("registro_profissional") or None,
                "insurance_plan_raw_name": item.get("convenio") or None,
                "local_name": item.get("local_atendimento") or None,
                "tipo_paciente": item.get("tipo_paciente") or None,
                "scheduled_at": item.get("data_agendamento"),  # pydantic converte ISO datetime/date sozinho
                "duration_minutes": item.get("duracao_minutos"),
                "status": item.get("status", ""),
                "procedure_code": item.get("codigo_procedimento") or None,
                "cid_code": item.get("cid") or None,
                "external_id": item.get("codigo_agendamento") or None,
            }
            row = RawAppointmentRow.model_validate(mapped)
            results.append(AgendaRowParseResult.ok(row_number, row))
        except (ValidationError, TypeError) as exc:
            results.append(AgendaRowParseResult.failed(row_number, exc))
    return results
