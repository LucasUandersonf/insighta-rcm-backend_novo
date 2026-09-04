"""
app/worker/parsers/xml_parser.py

DECISÃO DE SEGURANÇA — defusedxml em vez de xml.etree puro
-------------------------------------------------------------------------
Arquivo XML vindo de fora (SFTP de terceiro) é justamente o tipo de
input que pode trazer um ataque de XML External Entity (XXE) ou "billion
laughs" (entity expansion) se parseado com xml.etree.ElementTree padrão,
que resolve DTDs/entities por padrão. defusedxml.ElementTree tem a MESMA
API do etree, mas desabilita resolução de entidades externas e
expansão de entidades recursivas — é literalmente uma troca de import,
sem reescrever lógica de parsing, e fecha um vetor real de DevSecOps
(o briefing pede explicitamente proteção contra esse tipo de input).
"""
from datetime import date, datetime

from defusedxml import ElementTree as ET
from pydantic import ValidationError

from app.worker.schemas import RawBillingRow, RowParseResult


def parse(raw_bytes: bytes) -> list[RowParseResult]:
    root = ET.fromstring(raw_bytes)  # defusedxml lança exceção em payload malicioso, antes de expandir qualquer entidade

    results: list[RowParseResult] = []
    for row_number, atendimento in enumerate(root.findall(".//atendimento"), start=1):
        try:
            mapped = {
                "patient_cpf": _text(atendimento, "cpfPaciente"),
                "patient_name": _text(atendimento, "nomePaciente"),
                # Opcional (achado F-02, Auditoria Go-Live) — _text devolve
                # "" quando a tag não existe no XML; convertido para None
                # para não virar "profissional com nome vazio" na
                # normalização (ver normalization_service.py).
                "professional_name": _text(atendimento, "nomeProfissional") or None,
                "professional_registry": _text(atendimento, "registroProfissional") or None,
                "insurance_plan_raw_name": _text(atendimento, "convenio"),
                "procedure_code": _text(atendimento, "codigoProcedimento"),
                "cid_code": _text(atendimento, "cid"),
                "charged_value": _text(atendimento, "valorCobrado").replace(",", "."),
                "service_date": _parse_iso_date(_text(atendimento, "dataAtendimento")),
                # Campos do template estendido (Fase de "Templates de
                # Integração") — mesmo padrão camelCase das demais tags,
                # também opcionais.
                "local_name": _text(atendimento, "localAtendimento") or None,
                "tipo_paciente": _text(atendimento, "tipoPaciente") or None,
                "guia_tipo": _text(atendimento, "guiaTipo") or None,
                "guia_numero": _text(atendimento, "guiaNumero") or None,
                "guia_senha": _text(atendimento, "guiaSenha") or None,
            }
            row = RawBillingRow.model_validate(mapped)
            results.append(RowParseResult.ok(row_number, row))
        except (ValidationError, ValueError, AttributeError) as exc:
            results.append(RowParseResult.failed(row_number, exc))
    return results


def _text(element, tag: str) -> str:
    node = element.find(tag)
    return (node.text or "").strip() if node is not None else ""


def _parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()
