"""
app/worker/parsers/agenda_xml_parser.py

Formato XML do Template de Integração "Agenda" — mesma decisão de
segurança de xml_parser.py (defusedxml, ver docstring lá) e mesmo padrão
camelCase de tag. `<agendamento>` é o elemento repetido (equivalente ao
`<atendimento>` do Faturamento); `dataAgendamento` é um datetime/date ISO
completo (mesma convenção do agenda_json_parser.py), não data e hora em
tags separadas.
"""
from defusedxml import ElementTree as ET
from pydantic import ValidationError

from app.worker.schemas import AgendaRowParseResult, RawAppointmentRow


def parse(raw_bytes: bytes) -> list[AgendaRowParseResult]:
    root = ET.fromstring(raw_bytes)

    results: list[AgendaRowParseResult] = []
    for row_number, agendamento in enumerate(root.findall(".//agendamento"), start=1):
        try:
            mapped = {
                "patient_cpf": _text(agendamento, "cpfPaciente") or None,
                "patient_name": _text(agendamento, "nomePaciente"),
                "professional_name": _text(agendamento, "nomeProfissional") or None,
                "professional_registry": _text(agendamento, "registroProfissional") or None,
                "insurance_plan_raw_name": _text(agendamento, "convenio") or None,
                "local_name": _text(agendamento, "localAtendimento") or None,
                "tipo_paciente": _text(agendamento, "tipoPaciente") or None,
                "scheduled_at": _text(agendamento, "dataAgendamento"),
                "duration_minutes": _optional_int(_text(agendamento, "duracaoMinutos")),
                "status": _text(agendamento, "status"),
                "procedure_code": _text(agendamento, "codigoProcedimento") or None,
                "cid_code": _text(agendamento, "cid") or None,
                "external_id": _text(agendamento, "codigoAgendamento") or None,
            }
            row = RawAppointmentRow.model_validate(mapped)
            results.append(AgendaRowParseResult.ok(row_number, row))
        except (ValidationError, ValueError, AttributeError) as exc:
            results.append(AgendaRowParseResult.failed(row_number, exc))
    return results


def _text(element, tag: str) -> str:
    node = element.find(tag)
    return (node.text or "").strip() if node is not None else ""


def _optional_int(value: str) -> int | None:
    return int(value) if value else None
