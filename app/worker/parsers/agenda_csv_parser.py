"""
app/worker/parsers/agenda_csv_parser.py

Parser do Template de Integração "Agenda" — ver DECISÃO em
app/sql/019_agenda_ingestion.sql e docstring de RawAppointmentRow
(app/worker/schemas.py). Mesmo estilo de csv_parser.py (stdlib
csv.DictReader, ';' como separador, utf-8-sig tolera BOM de export
Windows) — arquivo próprio, não uma opção a mais dentro de csv_parser.py,
porque o cabeçalho esperado e o schema de destino (RawAppointmentRow, não
RawBillingRow) são inteiramente diferentes.

Diferente de Faturamento (uma única coluna `data_atendimento`), Agenda
tem DATA e HORA em colunas separadas — um relatório de agenda de
verdade lista o horário marcado, não só o dia (ver
MODERNANET_REFERENCIA.md, módulo Agenda). `hora_agendamento` é OPCIONAL:
sem ela, assume meia-noite (00:00) — mesmo critério de "melhor aceitar
com um dado a menos do que rejeitar a linha inteira" já usado em
professional_name/local_name.

Só CSV está implementado nesta primeira versão do template — XML/JSON
de Agenda ficam para quando um cliente/ERP concreto precisar (mesmo
critério incremental de app/worker/parsers/xml_parser.py ter sido
adicionado só quando o formato Faturamento precisou dele).
"""
import csv
import io
from datetime import date, datetime

from pydantic import ValidationError

from app.worker.schemas import AgendaRowParseResult, RawAppointmentRow

_EXPECTED_HEADERS = {
    "cpf_paciente": "patient_cpf",
    "nome_paciente": "patient_name",
    "nome_profissional": "professional_name",
    "registro_profissional": "professional_registry",
    "convenio": "insurance_plan_raw_name",
    "local_atendimento": "local_name",
    "tipo_paciente": "tipo_paciente",
    "duracao_minutos": "duration_minutes",
    "status": "status",
    "codigo_procedimento": "procedure_code",
    "cid": "cid_code",
    "codigo_agendamento": "external_id",
}

_OPTIONAL_STRING_FIELDS = (
    "professional_name",
    "professional_registry",
    "insurance_plan_raw_name",
    "local_name",
    "tipo_paciente",
    "procedure_code",
    "cid_code",
    "external_id",
)


def parse(raw_bytes: bytes) -> list[AgendaRowParseResult]:
    text = raw_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")

    results: list[AgendaRowParseResult] = []
    for row_number, raw_row in enumerate(reader, start=1):
        try:
            mapped = {
                canonical_field: raw_row.get(csv_header, "").strip()
                for csv_header, canonical_field in _EXPECTED_HEADERS.items()
            }
            for field in _OPTIONAL_STRING_FIELDS:
                mapped[field] = mapped[field] or None

            duration_raw = raw_row.get("duracao_minutos", "").strip()
            mapped["duration_minutes"] = int(duration_raw) if duration_raw else None

            mapped["scheduled_at"] = _combine_br_date_time(
                raw_row.get("data_agendamento", "").strip(),
                raw_row.get("hora_agendamento", "").strip(),
            )

            row = RawAppointmentRow.model_validate(mapped)
            results.append(AgendaRowParseResult.ok(row_number, row))
        except (ValidationError, ValueError) as exc:
            results.append(AgendaRowParseResult.failed(row_number, exc))
    return results


def _combine_br_date_time(date_str: str, time_str: str) -> datetime:
    parsed_date = _parse_br_date(date_str)
    hour, minute = _parse_br_time(time_str)
    return datetime(parsed_date.year, parsed_date.month, parsed_date.day, hour, minute)


def _parse_br_date(value: str) -> date:
    return datetime.strptime(value, "%d/%m/%Y").date()


def _parse_br_time(value: str) -> tuple[int, int]:
    """Vazia -> meia-noite (00:00) — ver docstring do módulo. Aceita
    tanto "HH:MM" quanto "HH:MM:SS" (alguns exports de ERP incluem
    segundos, sempre "00")."""
    if not value:
        return 0, 0
    parts = value.split(":")
    return int(parts[0]), int(parts[1])
