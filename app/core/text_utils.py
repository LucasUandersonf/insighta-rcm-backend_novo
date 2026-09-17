"""
app/core/text_utils.py

Extraído de normalization_service.py (Etapa 2) porque agora tem um
segundo consumidor: InsurancePlanService, ao criar um convênio novo pela
tela de Convênios, precisa gerar o mesmo normalized_key que a Etapa 2
usaria para casar um plano importado de arquivo — os dois caminhos
(cadastro manual e importação automática) têm que convergir para a
MESMA forma canônica, senão um plano cadastrado manualmente como "Amil
S450" nunca casaria com "AMIL S-450" vindo de um arquivo importado depois.

DECISÃO — Achados 10/11 da Auditoria de Templates e Insights: mesma
lógica, dois consumidores
-------------------------------------------------------------------
`sanitize_member_card_value`/`normalize_item_type` nasceram no Template
de Faturamento (ingestão em massa, ver app/worker/schemas.py), mas
`Billing.member_card_number`/`item_type` também são gravados pelo
endpoint manual (`BillingCreateRequest`, app/schemas/billing.py) — a
MESMA coluna, escrita por dois caminhos diferentes. A Rodada 1 da
auditoria blindou só a ingestão; a Rodada 2 achou que o caminho manual
ficou sem a mesma proteção. Extraídas pra cá pelo mesmo motivo de
`slugify` acima: dois consumidores precisam da MESMA forma canônica,
nunca duas implementações que podem divergir com o tempo.
"""
import re
import unicodedata

from app.models.billing import ITEM_TYPE_VALUES

# Escopo completo de pessoa física (ver app/sql/058_patient_full_identity.sql)
# — vocabulário fechado de sexo biológico, mesmo critério de
# ITEM_TYPE_VALUES/TIPO_PACIENTE_VALUES: pequeno, fechado, validado aqui
# (não por CHECK de banco) para a mensagem de erro poder listar as opções
# válidas.
SEX_VALUES = ("M", "F")

_SEX_ALIASES = {
    "m": "M",
    "masculino": "M",
    "homem": "M",
    "male": "M",
    "f": "F",
    "feminino": "F",
    "mulher": "F",
    "female": "F",
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def slugify(value: str) -> str:
    """"UNIMED NAC." -> "unimed_nac". Remove acentos, baixa a caixa, troca
    tudo que não é [a-z0-9] por underscore."""
    ascii_only = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    ascii_only = ascii_only.lower().strip()
    return re.sub(r"[^a-z0-9]+", "_", ascii_only).strip("_")


def strip_accents_lower(value: str) -> str:
    """Remove acentos, baixa a caixa, tira espaço nas pontas — mesmo
    algoritmo usado pelos aliases dos Templates de Faturamento/Agenda
    (ver app/worker/schemas.py). Diferente de `slugify`: não troca
    espaço/pontuação por underscore sozinho (quem chama decide se
    precisa disso), então não serve pra gerar chave de agrupamento —
    só para comparação case/acento-insensível."""
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower().strip()


def normalize_item_type(value: str | None) -> str | None:
    """Valida/normaliza `item_type` (procedimento/material_opme/taxa/
    diaria/medicamento — ver ITEM_TYPE_VALUES em app/models/billing.py)
    contra o vocabulário fechado. Levanta ValueError (que cada chamador
    converte num erro de validação no seu próprio formato — linha
    rejeitada na ingestão, 422 no endpoint manual) quando o valor não é
    reconhecido — nunca deixa passar um valor fora do vocabulário pro
    CHECK constraint do banco rejeitar com um erro genérico."""
    if value is None or value == "":
        return None
    normalized = strip_accents_lower(value).replace(" ", "_").replace("-", "_")
    if normalized not in ITEM_TYPE_VALUES:
        raise ValueError(f"item_type '{value}' não reconhecido — use um de: {', '.join(ITEM_TYPE_VALUES)}")
    return normalized


def sanitize_member_card_value(value: str) -> str | None:
    """Achado 1 da Auditoria (crítico) — Faturamento e o demonstrativo de
    Glosa vêm de DOIS sistemas diferentes (ERP da clínica vs. sistema da
    operadora), quase nunca formatando o mesmo número de carteirinha do
    mesmo jeito ("0012.345678.90-1" vs "001234567890 1"). Sem isso, a
    chave de conciliação alternativa por carteirinha (ver DECISÃO em
    RawDenialRow, app/worker/schemas.py) comparava string exata e
    falhava silenciosamente exatamente no cenário que ela foi criada
    para resolver. Mantém só caracteres alfanuméricos, em caixa alta."""
    cleaned = "".join(ch for ch in value if ch.isalnum()).upper()
    return cleaned or None


def validate_cpf_checksum(digits: str) -> None:
    """Valida os dois dígitos verificadores do CPF (algoritmo mod-11 da
    Receita Federal) — levanta ValueError quando o CPF tem 11 dígitos mas
    o dígito verificador não bate (typo de digitação, outro número de 11
    dígitos colado no campo errado etc.) ou é uma sequência de dígito
    repetido (000.000.000-00, 111.111.111-11...— matematicamente "passa"
    o checksum para alguns casos, mas nunca é um CPF emitido de verdade,
    mesma exclusão que qualquer validador de produção aplica). CPF é a
    chave de deduplicação de paciente (ver
    NormalizationService._get_or_create_patient) — um valor
    estruturalmente inválido nunca deveria virar identidade de paciente,
    silenciosamente fundindo ou separando pessoas reais."""
    if len(digits) != 11 or digits == digits[0] * 11:
        raise ValueError("CPF inválido — precisa ter 11 dígitos e dígito verificador correto.")

    def _check_digit(nums: str, start_weight: int) -> str:
        total = sum(int(n) * w for n, w in zip(nums, range(start_weight, 1, -1)))
        remainder = total % 11
        return str(0 if remainder < 2 else 11 - remainder)

    dv1 = _check_digit(digits[:9], 10)
    dv2 = _check_digit(digits[:9] + dv1, 11)
    if digits[9] != dv1 or digits[10] != dv2:
        raise ValueError("CPF inválido — dígito verificador não confere.")


def sanitize_phone_value(value: str) -> str | None:
    """Telefone — só dígitos (DDD + número), mesmo raciocínio de
    sanitize_member_card_value: nunca grava formatado ("(11) 98888-7777")
    para não quebrar comparação depois. Aceita 10 (fixo) ou 11 (celular)
    dígitos; qualquer outra contagem é rejeitada — mais barato rejeitar
    na ingestão do que descobrir depois que a lista de reativação tem
    número impossível de discar."""
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        return None
    if len(digits) not in (10, 11):
        raise ValueError("Telefone deve conter 10 ou 11 dígitos (DDD + número).")
    return digits


def validate_email_value(value: str) -> str:
    """Validação leve de formato (nunca confirma entregabilidade) —
    rejeita lixo óbvio (sem "@", sem domínio) sem tentar ser um
    validador RFC 5322 completo, que rejeitaria e-mail real válido por
    excesso de rigor."""
    cleaned = value.strip().lower()
    if not _EMAIL_RE.match(cleaned):
        raise ValueError("E-mail em formato inválido.")
    return cleaned


def normalize_sex_value(value: str) -> str:
    normalized = _SEX_ALIASES.get(strip_accents_lower(value))
    if normalized is None:
        raise ValueError(f"sexo '{value}' não reconhecido — use um de: {', '.join(SEX_VALUES)}")
    return normalized
