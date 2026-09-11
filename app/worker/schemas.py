"""
app/worker/schemas.py

DECISÃO — Schema canônico único, independente do formato de origem
-------------------------------------------------------------------------
CSV, XML e JSON podem representar a mesma informação com nomes de coluna/
tag/chave completamente diferentes de sistema para sistema de clínica.
Em vez de a Etapa 2 (normalização, fora do escopo deste worker) precisar
conhecer os três formatos, cada parser (csv_parser.py, xml_parser.py,
json_parser.py) já traduz para ESTE schema comum antes de gravar em
ingestion_raw_rows. Resultado: o resto do pipeline lida com um único
formato interno, e adicionar um 4º formato de origem no futuro (ex: um
webhook de um PEP moderno) significa escrever só mais um parser, sem
tocar em mais nada.

Esta é uma validação ESTRUTURAL (a linha tem os campos no formato certo?
o valor é um número válido?) — não é a mesma coisa que a normalização de
negócio da Etapa 2 (ex: casar "UNIMED NAC." com o convênio certo). Isso
fica claro pelo nome: RawBillingRow, não BillingRow.

SEGUNDO TEMPLATE — RawAppointmentRow (Agenda)
-------------------------------------------------------------------------
Faturamento e Agenda são dois TEMPLATES DE INTEGRAÇÃO diferentes (ver
conversa/PLANO_ADEQUACAO_TISS.md): uma linha de Faturamento é sempre um
atendimento JÁ OCORRIDO com valor cobrado; uma linha de Agenda é um
agendamento que muda de estado com o tempo e não gera cobrança nenhuma
por si só. Por isso RawAppointmentRow é uma classe SEPARADA de
RawBillingRow (campos obrigatórios e o que a normalização faz com cada
uma são diferentes), mas compartilha os mesmos helpers de normalização
de dado sujo (_strip_accents_lower, aliases de tipo_paciente) — o
"schema canônico único por formato de origem" vale para os DOIS
templates, não só para Faturamento.
"""
import unicodedata
from datetime import date, datetime

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.models.appointment import TIPO_PACIENTE_VALUES, VISIT_TYPE_VALUES
from app.models.billing import ITEM_TYPE_VALUES
from app.models.guia import GUIA_TIPOS


def _strip_accents_lower(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower().strip()


# Aliases comuns além do valor canônico — um export de ERP raramente usa
# o nome interno do nosso banco ("pronto_socorro"), mais provável vir
# "PS", "Pronto Socorro", "Pronto-Socorro" etc. Igual para tipo de guia
# ("SP/SADT" é como o padrão TISS chama o que guardamos como "sadt").
#
# CORREÇÃO — cada dict PRECISA mapear o próprio valor canônico para si
# mesmo (ex: "pronto_socorro" -> "pronto_socorro"), não só os aliases de
# entrada. Motivo: normalize_row() (normalization_service.py) revalida
# RawBillingRow a partir do `payload` JÁ NORMALIZADO gravado em
# ingestion_raw_rows por save_raw_rows() — ou seja, o validador roda uma
# SEGUNDA vez sobre o valor que ele mesmo produziu da primeira vez (no
# parser). Sem a auto-referência, um valor canônico como "pronto_socorro"
# (sem espaço/acento, já é a própria chave depois de _strip_accents_lower)
# não batia em nenhum alias e a linha inteira, já corretamente
# normalizada, era rejeitada na segunda passada — bug pego por
# tests/integration/test_ingestion_extended_template.py.
_TIPO_PACIENTE_ALIASES = {
    "ambulatorial": "ambulatorial",
    "ambulatorio": "ambulatorial",
    "amb": "ambulatorial",
    "internacao": "internacao",
    "internado": "internacao",
    "int": "internacao",
    "pronto socorro": "pronto_socorro",
    "pronto-socorro": "pronto_socorro",
    "prontosocorro": "pronto_socorro",
    "pronto_socorro": "pronto_socorro",
    "ps": "pronto_socorro",
}
_GUIA_TIPO_ALIASES = {
    "consulta": "consulta",
    "sadt": "sadt",
    "sp/sadt": "sadt",
    "sp sadt": "sadt",
    "resumo internacao": "resumo_internacao",
    "resumo de internacao": "resumo_internacao",
    "resumo_internacao": "resumo_internacao",
    "internacao": "resumo_internacao",
    "honorario": "honorario",
    "honorario individual": "honorario",
    "honorario_individual": "honorario",
    "honorarios": "honorario",
}

# Vocabulário do Template de Integração "Agenda" (ver
# app/sql/019_agenda_ingestion.sql) — os 5 status que aparecem em
# praticamente toda tela de agenda de ERP pesquisada (Moderna, Feegow,
# iClinic), mapeados para o vocabulário que já existe em
# Appointment.status (ver app/models/appointment.py e a CHECK constraint
# de 001_init_schema.sql, que já incluía 'confirmed' mesmo antes de o
# produto ter um canal de confirmação próprio — ver DECISÃO em
# AppointmentUpdateRequest sobre por que a API de edição manual não
# expõe esse estado; a ingestão em massa pode receber um agendamento
# JÁ confirmado pelo ERP de origem sem que isso dependa desse canal).
_AGENDA_STATUS_ALIASES = {
    "agendado": "scheduled",
    "agendada": "scheduled",
    "marcado": "scheduled",
    "marcada": "scheduled",
    "scheduled": "scheduled",
    "confirmado": "confirmed",
    "confirmada": "confirmed",
    "confirmed": "confirmed",
    "atendido": "completed",
    "atendida": "completed",
    "realizado": "completed",
    "realizada": "completed",
    "compareceu": "completed",
    "completed": "completed",
    "faltou": "no_show",
    "falta": "no_show",
    "nao compareceu": "no_show",
    "no_show": "no_show",
    "no-show": "no_show",
    "cancelado": "cancelled",
    "cancelada": "cancelled",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}


def _normalize_tipo_paciente_value(v: str) -> str:
    normalized = _TIPO_PACIENTE_ALIASES.get(_strip_accents_lower(v))
    if normalized is None:
        raise ValueError(f"tipo_paciente '{v}' não reconhecido — use um de: {', '.join(TIPO_PACIENTE_VALUES)}")
    return normalized


def _sanitize_cpf_value(v: str) -> str | None:
    digits = "".join(ch for ch in v if ch.isdigit())
    return digits or None


class RawBillingRow(BaseModel):
    patient_cpf: str | None = None
    patient_name: str = Field(min_length=1, max_length=255)
    # Profissional executante — OPCIONAL porque nem todo layout de origem
    # traz esse dado por linha (ex: alguns exports legados só têm o
    # procedimento e o convênio). Quando ausente, o atendimento é
    # normalizado sem professional_id (mesmo comportamento de hoje) — não
    # é um erro estrutural, só um dado que a Agenda & Capacidade não vai
    # conseguir atribuir a ninguém específico.
    #
    # CORREÇÃO (Auditoria Go-Live, achado F-02): antes, o único jeito de
    # um Profissional existir no sistema era uma tela de CRUD manual —
    # incompatível com "o SaaS opera exclusivamente sobre dados
    # consolidados do ERP" (mesma razão pela qual /patients e
    # /professionals foram removidas do frontend). Agora o profissional
    # entra pelo mesmo caminho que o paciente: extraído da própria linha
    # de faturamento (ver normalization_service._get_or_create_professional).
    professional_name: str | None = None
    professional_registry: str | None = None
    insurance_plan_raw_name: str = Field(min_length=1, max_length=255)
    procedure_code: str = Field(min_length=1, max_length=20)
    cid_code: str | None = None
    # gt=0 sempre existiu. O teto `le` é a rede de segurança adicionada na
    # Auditoria Go-Live (achado F-01, recomendação c): um bug de parsing
    # de moeda (separador de milhar/decimal trocado) tipicamente infla o
    # valor por um fator de 100x-1000x — nunca produz um valor "só um
    # pouco" errado. R$ 500.000,00 é bem acima de qualquer procedimento
    # TUSS individual plausível (mesmo cirurgias/oncologia de alta
    # complexidade ficam ordens de grandeza abaixo disso por item de
    # faturamento), então o teto some com corrupção grosseira sem nunca
    # rejeitar um valor legítimo. Isso é defesa em profundidade, não
    # substitui a correção da normalização em si (ver csv_parser.py).
    charged_value: float = Field(gt=0, le=500_000)
    service_date: date

    # --- Campos do template estendido (Fase de "Templates de Integração"
    # — ver conversa/PLANO_ADEQUACAO_TISS.md) — todos OPCIONAIS, o mesmo
    # critério de professional_name: um export que não tem essas colunas
    # continua funcionando exatamente como antes. ---
    local_name: str | None = None
    tipo_paciente: str | None = None
    guia_tipo: str | None = None
    guia_numero: str | None = None
    guia_senha: str | None = None

    # --- Campos novos, achado do Dicionário de Dados (auditoria BI/Dados
    # desta rodada) — mesmo critério de opcionalidade dos demais. ---
    # Unidades do mesmo procedimento nesta linha. default=1 (não None):
    # um arquivo sem essa coluna significa "1 unidade", nunca "não sei" —
    # é exatamente o comportamento implícito que o sistema sempre teve.
    quantidade: int = Field(default=1, gt=0, le=1000)
    numero_carteirinha: str | None = None
    # Tabela do procedimento (padrão TISS: 18=CBHPM, 19/20=tabela própria,
    # 22=TUSS) — mesma coluna que já existe em Guia.tabela_procedimento
    # (app/models/guia.py), nunca antes alimentada por nenhum template de
    # ingestão. Sem validação de enum fechado aqui de propósito: o modelo
    # Guia também não valida (String livre) — convênios às vezes usam
    # código de tabela própria fora da lista pública.
    tabela_procedimento: str | None = None
    tipo_item: str | None = None
    # ge=0 (não gt=0): 0 é um valor real (procedimento sem coparticipação
    # nesta linha específica, mas o campo foi informado mesmo assim).
    valor_coparticipacao: float | None = Field(default=None, ge=0, le=500_000)

    @field_validator("patient_cpf")
    @classmethod
    def sanitize_cpf(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return _sanitize_cpf_value(v)

    @field_validator("numero_carteirinha", "tabela_procedimento")
    @classmethod
    def blank_optional_to_none(cls, v: str | None) -> str | None:
        return v or None

    @field_validator("tipo_item")
    @classmethod
    def normalize_tipo_item(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        normalized = _strip_accents_lower(v).replace(" ", "_").replace("-", "_")
        if normalized not in ITEM_TYPE_VALUES:
            raise ValueError(f"tipo_item '{v}' não reconhecido — use um de: {', '.join(ITEM_TYPE_VALUES)}")
        return normalized

    @field_validator("tipo_paciente")
    @classmethod
    def normalize_tipo_paciente(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return _normalize_tipo_paciente_value(v)

    @field_validator("guia_tipo")
    @classmethod
    def normalize_guia_tipo(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        normalized = _GUIA_TIPO_ALIASES.get(_strip_accents_lower(v))
        if normalized is None:
            raise ValueError(f"guia_tipo '{v}' não reconhecido — use um de: {', '.join(GUIA_TIPOS)}")
        return normalized

    @model_validator(mode="after")
    def check_guia_fields_consistency(self) -> "RawBillingRow":
        # guia_numero/guia_senha sem guia_tipo não tem como virar uma
        # Guia de verdade (Guia.tipo é obrigatório) — melhor rejeitar a
        # linha explicitamente do que silenciosamente ignorar número/
        # senha que o arquivo trouxe.
        if (self.guia_numero or self.guia_senha) and not self.guia_tipo:
            raise ValueError("guia_numero/guia_senha informados sem guia_tipo — guia_tipo é obrigatório para registrar a guia.")
        return self


class RawAppointmentRow(BaseModel):
    """
    Schema canônico do Template de Integração "Agenda" — ver docstring do
    módulo e app/sql/019_agenda_ingestion.sql. Deliberadamente usa os
    MESMOS nomes de campo de identidade (patient_cpf/patient_name/
    professional_name/professional_registry/local_name) que RawBillingRow
    onde o conceito é idêntico — NormalizationService._get_or_create_patient/
    _get_or_create_professional/_get_or_create_local são reaproveitados
    SEM DUPLICAÇÃO entre os dois templates por causa disso (Python
    resolve por nome de atributo, não por classe declarada).
    """

    patient_cpf: str | None = None
    patient_name: str = Field(min_length=1, max_length=255)
    professional_name: str | None = None
    professional_registry: str | None = None
    # Diferente de RawBillingRow: uma linha de Agenda pode não ter
    # convênio confirmado ainda (agendamento feito antes da confirmação
    # de cobertura) — OPCIONAL aqui, sempre OBRIGATÓRIO em Faturamento.
    insurance_plan_raw_name: str | None = None
    local_name: str | None = None
    tipo_paciente: str | None = None
    scheduled_at: datetime
    duration_minutes: int | None = Field(default=None, gt=0)
    status: str
    procedure_code: str | None = None
    cid_code: str | None = None
    # Código do agendamento no sistema de origem — chave de UPSERT (ver
    # app/models/appointment.py Appointment.external_id). Sem ele, cada
    # reimportação do mesmo relatório cria um agendamento duplicado
    # (limitação aceita e documentada, mesma classe de limitação de
    # ProfessionalRepository.get_by_name sem registro profissional).
    external_id: str | None = None

    # --- Campos novos, achado do Dicionário de Dados (auditoria BI/Dados
    # desta rodada) — mesmo critério de opcionalidade dos demais. ---
    # Quando o agendamento foi de fato MARCADO no ERP (≠ scheduled_at, que
    # é a data/hora da CONSULTA em si) — habilita medir tempo de espera.
    criado_em: datetime | None = None
    tipo_consulta: str | None = None
    motivo_cancelamento: str | None = None
    # Canal por onde este agendamento foi marcado — texto livre de
    # propósito (telefone/whatsapp/site/presencial são os mais comuns,
    # mas não um vocabulário fechado universal como status/tipo_paciente).
    canal_agendamento: str | None = None

    @field_validator("patient_cpf")
    @classmethod
    def sanitize_cpf(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return _sanitize_cpf_value(v)

    @field_validator("tipo_paciente")
    @classmethod
    def normalize_tipo_paciente(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return _normalize_tipo_paciente_value(v)

    @field_validator("motivo_cancelamento", "canal_agendamento")
    @classmethod
    def blank_optional_to_none(cls, v: str | None) -> str | None:
        return v or None

    @field_validator("tipo_consulta")
    @classmethod
    def normalize_tipo_consulta(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        normalized = _strip_accents_lower(v).replace(" ", "_").replace("-", "_")
        aliases = {"primeira_consulta": "primeira_consulta", "primeira": "primeira_consulta", "1a_consulta": "primeira_consulta", "retorno": "retorno"}
        resolved = aliases.get(normalized)
        if resolved is None:
            raise ValueError(f"tipo_consulta '{v}' não reconhecido — use um de: {', '.join(VISIT_TYPE_VALUES)}")
        return resolved

    @field_validator("status")
    @classmethod
    def normalize_status(cls, v: str) -> str:
        normalized = _AGENDA_STATUS_ALIASES.get(_strip_accents_lower(v))
        if normalized is None:
            raise ValueError(f"status '{v}' não reconhecido — use um de: agendado, confirmado, atendido, faltou, cancelado")
        return normalized


class RawDenialRow(BaseModel):
    """
    Schema canônico do TERCEIRO Template de Integração, "Glosa" — o
    demonstrativo de pagamento/glosa que a operadora devolve depois de
    processar um lote de guias (ver achado do Raio-X da Sala de Comando:
    pct_conciliado estava em 34,4% porque `settle_billing` existe mas
    nada em massa alimentava ele). Diferente de Faturamento/Agenda, esta
    linha NUNCA cria um Appointment/Patient novo — ela CASA com um
    Billing que já existe (criado quando a guia foi originalmente
    cobrada) e registra o resultado real do pagamento.

    DECISÃO — casamento por (convênio, guia_numero, procedure_code opcional),
    nunca por um ID interno do nosso banco
    -------------------------------------------------------------------------
    O demonstrativo da operadora não tem ideia do nosso billing_id (UUID
    interno) — ele fala a língua da guia (numero + tabela/procedimento),
    exatamente o mesmo vocabulário que o template de Faturamento já grava
    em Guia.numero via guia_numero (ver RawBillingRow). `procedure_code` é
    OPCIONAL: só é exigido quando a guia agrupa mais de uma linha de
    billing (uma SADT com vários procedimentos) — nesse caso, sem ele, a
    linha é rejeitada por ambiguidade em vez de adivinhar qual procedimento
    o pagamento se refere (mesmo princípio de "nunca inventar" de todo o
    motor de risco).

    DECISÃO — chave de conciliação ALTERNATIVA por número da carteirinha
    (achado do Dicionário de Dados)
    -------------------------------------------------------------------------
    Nem todo demonstrativo de operadora devolve o número da guia (alguns
    conciliam só por beneficiário + procedimento + competência). Exigir
    guia_numero sempre rejeitaria 100% das linhas desses convênios, mesmo
    quando a guia foi corretamente registrada no Faturamento. `numero_carteirinha`
    é uma alternativa real: o Faturamento já grava esse dado por linha (ver
    RawBillingRow.numero_carteirinha / Billing.member_card_number,
    achado desta mesma auditoria), então ele já existe no banco quando o
    demonstrativo chega. Exatamente UM dos dois (`guia_numero` ou
    `numero_carteirinha`) precisa vir preenchido — nunca os dois vazios
    (linha sem qualquer chave de casamento é rejeitada, nunca "adivinhada"
    por outro critério como nome/data). Quando `guia_numero` vem
    preenchido, ele é a chave PRIMÁRIA (mais específica: já aponta para
    UMA guia); `numero_carteirinha` só é usado quando `guia_numero` está
    ausente. `procedure_code` desambigua os dois caminhos do mesmo jeito
    (uma carteirinha pode ter várias cobranças abertas no mesmo convênio).

    DECISÃO — motivo de glosa NÃO entra em Billing.denial_reasons, entra
    em core.glosas (Glosa REAL — já existe, ver app/models/glosa.py)
    -------------------------------------------------------------------------
    `Billing.denial_reasons` guarda os motivos PREVISTOS pelo nosso
    próprio motor de risco (ex: "missing_cid") — misturar aí o motivo REAL
    que a operadora devolveu confundiria previsão com fato. `core.glosas`
    já existe exatamente para o fato ("a operadora negou/reduziu X") e já
    alimenta `GlosaService.get_reconciliation` (Previsto x Realizado,
    precisão/recall do motor de risco) — essa tabela está vazia hoje pela
    MESMA razão que received_value está: não existe caminho em massa que
    escreva nela, só o endpoint manual POST /glosas. Este template
    normaliza para as DUAS entidades a partir do mesmo demonstrativo:
    settle o Billing (received_value) e, quando `charged_value -
    received_value > 0`, registra a Glosa correspondente — nunca pede
    `valor_glosado` no arquivo (evita o arquivo contradizer os próprios
    valores cobrado/pago), deriva do que já está no Billing.
    """

    insurance_plan_raw_name: str = Field(min_length=1, max_length=255)
    # Nenhum dos dois é obrigatório isoladamente — ver DECISÃO acima sobre
    # chave de conciliação alternativa. `check_has_reconciliation_key`
    # garante que ao menos um venha preenchido.
    guia_numero: str | None = Field(default=None, max_length=50)
    numero_carteirinha: str | None = Field(default=None, max_length=50)
    procedure_code: str | None = None
    # ge=0 (não gt=0, diferente de charged_value): 0 é um valor real e
    # esperado aqui — glosa total, a operadora pagou zero pelo item.
    received_value: float = Field(ge=0, le=500_000)
    settlement_date: date | None = None
    # Motivo da glosa (Tabela 27 TISS/ANS) — OPCIONAIS: só existem
    # quando charged_value > received_value (houve glosa de fato) e nem
    # todo demonstrativo traz o código estruturado (às vezes só texto
    # livre) — mesma nulabilidade de Glosa.codigo_motivo/descricao_motivo.
    codigo_motivo: str | None = None
    descricao_motivo: str | None = None

    @field_validator("guia_numero", "numero_carteirinha", "procedure_code", "codigo_motivo", "descricao_motivo")
    @classmethod
    def blank_to_none(cls, v: str | None) -> str | None:
        return v or None

    @model_validator(mode="after")
    def check_has_reconciliation_key(self) -> "RawDenialRow":
        if not self.guia_numero and not self.numero_carteirinha:
            raise ValueError(
                "informe guia_numero ou numero_carteirinha — sem nenhuma das duas chaves não há como "
                "casar esta linha com um faturamento existente."
            )
        return self


class DenialRowParseResult(BaseModel):
    """Espelha AgendaRowParseResult, para RawDenialRow — ver docstring de
    RowParseResult para o contrato (sempre exatamente um entre `row`/
    `errors` preenchido)."""

    row_number: int
    row: RawDenialRow | None = None
    errors: list[str] | None = None

    @classmethod
    def ok(cls, row_number: int, row: RawDenialRow) -> "DenialRowParseResult":
        return cls(row_number=row_number, row=row, errors=None)

    @classmethod
    def failed(cls, row_number: int, exc: ValidationError | Exception) -> "DenialRowParseResult":
        if isinstance(exc, ValidationError):
            messages = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        else:
            messages = [str(exc)]
        return cls(row_number=row_number, row=None, errors=messages)


class AgendaRowParseResult(BaseModel):
    """
    Espelha RowParseResult, mas para RawAppointmentRow — ver docstring de
    lá para o contrato (sempre exatamente um entre `row`/`errors`
    preenchido). Classe separada em vez de reaproveitar RowParseResult
    diretamente porque `row` teria que aceitar DOIS tipos de model
    incompatíveis (Pydantic revalidaria uma instância de
    RawAppointmentRow contra o schema de RawBillingRow se o campo fosse
    tipado `RawBillingRow | None`, e falharia). `IngestionRepository.save_raw_rows`
    só acessa `.row_number`/`.row`/`.errors` por atributo — funciona com
    QUALQUER uma das duas classes, sem precisar saber qual é.
    """
    row_number: int
    row: RawAppointmentRow | None = None
    errors: list[str] | None = None

    @classmethod
    def ok(cls, row_number: int, row: RawAppointmentRow) -> "AgendaRowParseResult":
        return cls(row_number=row_number, row=row, errors=None)

    @classmethod
    def failed(cls, row_number: int, exc: ValidationError | Exception) -> "AgendaRowParseResult":
        if isinstance(exc, ValidationError):
            messages = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        else:
            messages = [str(exc)]
        return cls(row_number=row_number, row=None, errors=messages)


class RowParseResult(BaseModel):
    """
    Resultado da tentativa de parsear+validar UMA linha do arquivo.
    Sempre exatamente um entre `row` e `errors` é preenchido — nunca
    ambos, nunca nenhum. Isso é o contrato que ingestion_worker.py espera
    de todo parser, para poder gravar em ingestion_raw_rows de forma
    uniforme independente do formato original.
    """
    row_number: int
    row: RawBillingRow | None = None
    errors: list[str] | None = None

    @classmethod
    def ok(cls, row_number: int, row: RawBillingRow) -> "RowParseResult":
        return cls(row_number=row_number, row=row, errors=None)

    @classmethod
    def failed(cls, row_number: int, exc: ValidationError | Exception) -> "RowParseResult":
        if isinstance(exc, ValidationError):
            messages = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        else:
            messages = [str(exc)]
        return cls(row_number=row_number, row=None, errors=messages)
