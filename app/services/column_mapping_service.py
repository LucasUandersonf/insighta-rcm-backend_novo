"""
app/services/column_mapping_service.py

Mapeador Automático de Coluna — ver DECISÃO completa em
app/sql/021_ingestion_column_aliases.sql. Esta versão cobre só o
template de Faturamento em CSV: é onde cada campo canônico é um
passthrough 1:1 de uma única coluna do arquivo (EXPECTED_HEADERS em
csv_parser.py) — Agenda tem `scheduled_at` composto de DUAS colunas
(data + hora), o que exigiria uma UI de mapeamento many-to-one mais
complexa, fora de escopo por ora (mesmo critério incremental já usado
no resto do projeto: construído quando um cliente concreto precisar).

Função PURA (sem banco) — recebe os cabeçalhos do arquivo e os aliases
JÁ confirmados deste tenant (buscados por quem chama), devolve uma
sugestão. Nunca decide sozinha o que aplicar — só sugere; confirmar é
sempre uma ação explícita do usuário (POST /ingestion/column-aliases).
"""
import csv
import io
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.worker.parsers.csv_parser import EXPECTED_HEADERS


class EmptyCsvHeaderError(ValueError):
    """Arquivo vazio ou sem nenhuma linha de cabeçalho — não há o que
    sugerir (nem processar depois)."""


def extract_csv_headers(raw_bytes: bytes) -> list[str]:
    """Lê SÓ a primeira linha do CSV (o cabeçalho) — usado pelo preview
    (POST /ingestion/preview-headers), que nunca deveria processar/gravar
    nada, só inspecionar a estrutura de colunas do arquivo. Mesma
    decodificação de csv_parser.py (utf-8-sig, ';')."""
    text = raw_bytes.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text), delimiter=";")
    try:
        return next(reader)
    except StopIteration as exc:
        raise EmptyCsvHeaderError("Arquivo CSV vazio — nenhuma linha de cabeçalho encontrada.") from exc

# Campos OBRIGATÓRIOS de RawBillingRow (ver app/worker/schemas.py) — sem
# um cabeçalho reconhecido (padrão OU alias) para cada um destes, TODA
# linha do arquivo é rejeitada por validação estrutural. É essa lista
# que decide o que aparece como "pendente de mapear" na tela de revisão.
REQUIRED_CANONICAL_FIELDS = (
    "patient_name",
    "insurance_plan_raw_name",
    "procedure_code",
    "charged_value",
    "service_date",
)

# Rótulo legível em português — usado tanto na tela de revisão quanto
# como segundo alvo de comparação na sugestão (um cabeçalho "Convênio"
# bate melhor com o RÓTULO "Convênio" do que com o nome interno em
# inglês "insurance_plan_raw_name").
CANONICAL_FIELD_LABELS = {
    "patient_cpf": "CPF do paciente",
    "patient_name": "Nome do paciente",
    "professional_name": "Nome do profissional",
    "professional_registry": "Registro do profissional",
    "insurance_plan_raw_name": "Convênio",
    "procedure_code": "Código do procedimento",
    "cid_code": "CID",
    "charged_value": "Valor cobrado",
    "service_date": "Data do atendimento",
    "local_name": "Local de atendimento",
    "tipo_paciente": "Tipo de paciente",
    "guia_tipo": "Tipo de guia",
    "guia_numero": "Número da guia",
    "guia_senha": "Senha da guia",
}

# Todo campo canônico que o template de Faturamento reconhece (obrigatório
# OU opcional) — usado para VALIDAR um mapeamento antes de salvar (ver
# POST /ingestion/column-aliases): um `canonical_field` fora deste
# conjunto é sempre um erro de digitação/integração, nunca um campo
# "novo" válido que o parser simplesmente ainda não conhece.
ALL_CANONICAL_FIELDS = frozenset(EXPECTED_HEADERS.values())

# Abaixo disso, é melhor não sugerir nada (deixar "pendente de mapear")
# do que arriscar uma sugestão errada que o usuário aceita sem checar.
_MIN_SIMILARITY = 0.6


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    stripped = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return stripped.lower().replace(" ", "_").replace("-", "_").strip()


def _similarity(header: str, canonical_field: str) -> float:
    label = CANONICAL_FIELD_LABELS.get(canonical_field, "")
    by_field_name = SequenceMatcher(None, _normalize(header), _normalize(canonical_field)).ratio()
    by_label = SequenceMatcher(None, _normalize(header), _normalize(label)).ratio() if label else 0.0
    return max(by_field_name, by_label)


@dataclass
class ColumnMappingPreview:
    raw_headers: list[str]
    # {cabeçalho do arquivo: campo canônico} — só os pares com
    # similaridade >= _MIN_SIMILARITY; o usuário confirma ou corrige na
    # tela de revisão antes de qualquer coisa ser salva.
    suggested_mapping: dict[str, str]
    # Campos obrigatórios sem cabeçalho reconhecido (padrão, alias já
    # salvo, OU sugestão desta passada) — se não-vazio, o arquivo vai
    # rejeitar TODA linha até isso ser resolvido.
    unresolved_required_fields: list[str]


def suggest_mapping(raw_headers: list[str], existing_aliases: dict[str, str]) -> ColumnMappingPreview:
    """
    `existing_aliases` são os aliases JÁ confirmados deste tenant (ver
    IngestionColumnAliasRepository.get_mapping) — cabeçalhos que já
    batem com o padrão OU com um alias salvo não entram na sugestão
    (já estão resolvidos).
    """
    already_recognized = {**EXPECTED_HEADERS, **existing_aliases}
    # BUG evitado aqui: `recognized_fields` precisa vir só dos cabeçalhos
    # que de fato APARECEM neste arquivo — EXPECTED_HEADERS/existing_aliases
    # descrevem "cabeçalho X resolveria campo Y SE presente", não "campo Y
    # já está resolvido". Usar o dict inteiro sem filtrar por raw_headers
    # marcaria todo campo obrigatório como resolvido mesmo quando o
    # cabeçalho correspondente nunca apareceu no arquivo.
    recognized_fields = {already_recognized[h] for h in raw_headers if h in already_recognized}
    unmatched_headers = [h for h in raw_headers if h not in already_recognized]
    unmatched_fields = [f for f in REQUIRED_CANONICAL_FIELDS if f not in recognized_fields]

    suggested: dict[str, str] = {}
    for canonical_field in unmatched_fields:
        if not unmatched_headers:
            break
        scored = [(header, _similarity(header, canonical_field)) for header in unmatched_headers]
        best_header, best_score = max(scored, key=lambda item: item[1])
        if best_score >= _MIN_SIMILARITY:
            suggested[best_header] = canonical_field
            unmatched_headers.remove(best_header)

    unresolved = [f for f in unmatched_fields if f not in suggested.values()]
    return ColumnMappingPreview(raw_headers=raw_headers, suggested_mapping=suggested, unresolved_required_fields=unresolved)
