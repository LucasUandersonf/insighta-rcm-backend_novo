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
"""
from datetime import date

from pydantic import BaseModel, Field, ValidationError, field_validator


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

    @field_validator("patient_cpf")
    @classmethod
    def sanitize_cpf(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        digits = "".join(ch for ch in v if ch.isdigit())
        return digits or None


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
