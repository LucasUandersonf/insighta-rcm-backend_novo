"""
app/models/billing.py

Model ORM que espelha core.billing (ver 001_init_schema.sql). Note que
NÃO reimplementamos o RLS aqui em Python — o SQLAlchemy nem sabe que RLS
existe. A tabela sempre tem tenant_id como coluna normal; é o Postgres,
por baixo, que filtra as linhas. O ORM só precisa declarar o schema.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Vocabulário TISS/ANS para o tipo de item cobrado numa guia — OPME
# (Órteses/Próteses/Materiais Especiais) é destacado à parte porque é
# uma das maiores fontes de glosa de alto valor (achado do Dicionário de
# Dados) e hoje é indistinguível de um procedimento comum na Billing.
ITEM_TYPE_VALUES = ("procedimento", "material_opme", "taxa", "diaria", "medicamento")

# "Mapa de Dados Insighta" — Domínio Financeiro particular (Onda 1).
PAYMENT_METHOD_VALUES = ("dinheiro", "pix", "cartao_debito", "cartao_credito", "boleto")


class Billing(Base):
    __tablename__ = "billing"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="billing_quantity_check"),
        CheckConstraint(f"item_type IS NULL OR item_type IN {ITEM_TYPE_VALUES}", name="billing_item_type_check"),
        CheckConstraint(
            f"payment_method IS NULL OR payment_method IN {PAYMENT_METHOD_VALUES}", name="billing_payment_method_check"
        ),
        CheckConstraint("installments IS NULL OR installments >= 1", name="billing_installments_check"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    appointment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.appointments.id"), nullable=False)
    insurance_plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.insurance_plans.id"), nullable=False)
    charged_value: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    denial_risk_level: Mapped[str] = mapped_column(String(20), nullable=False, default="low")
    denial_reasons: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    value_saved_by_correction: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    # Valor efetivamente repassado pela operadora — NULL até a liquidação
    # do lote (ver BillingService.settle_billing). Comparado contra
    # ContractItem.agreed_price para detectar underpayment (Divergência de
    # Recebimento) — ver app/repositories/analytics_repository.py.
    received_value: Mapped[float | None] = mapped_column(Numeric(12, 2))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Guia TISS à qual este lançamento pertence (ver app/models/guia.py) —
    # NULLABLE porque todo billing vindo da ingestão em massa hoje não
    # tem noção de guia (o formato de arquivo ainda não carrega isso).
    # Uma guia pode agrupar N linhas de billing (ex.: SADT com vários
    # procedimentos na mesma guia).
    guia_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("core.guias.id"))
    # Unidades do mesmo procedimento cobradas nesta linha — achado do
    # Dicionário de Dados: sem isso, quantidade > 1 só cabia duplicando
    # linha (quebrando a chave guia+procedimento usada pelo Template de
    # Glosa). default=1 preserva o comportamento de sempre para todo
    # billing que nunca informou isso — nunca reinterpreta dado antigo.
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # Identificador do paciente NO CONVÊNIO (número da carteirinha) —
    # dado do convênio, não do paciente em si (por isso mora aqui, não em
    # Patient: o mesmo paciente pode ter carteirinhas diferentes por
    # convênio). NULLABLE: nem todo ERP de origem exporta isso.
    member_card_number: Mapped[str | None] = mapped_column(String(50))
    # Tipo do item cobrado (ver ITEM_TYPE_VALUES) — NULLABLE porque o
    # dado de origem raramente distingue isso hoje; quando informado,
    # habilita separar OPME (alto valor, alta taxa de glosa) do resto.
    item_type: Mapped[str | None] = mapped_column(String(20))
    # Valor cobrado DIRETO do paciente (coparticipação/franquia), à parte
    # do que o convênio paga — achado do Dicionário de Dados: hoje essa
    # fatia de receita não tem coluna nenhuma no sistema. NULLABLE (nem
    # todo convênio/procedimento tem coparticipação), nunca confundido
    # com charged_value (que continua sendo só a parte cobrada do convênio).
    coparticipation_value: Mapped[float | None] = mapped_column(Numeric(12, 2))
    # Épico F4.2 do Plano Diretor ("Fechar lacunas operacionais") —
    # confirmação de que coparticipation_value foi DE FATO recebido do
    # paciente, não só cobrado no papel. NULL = ainda não confirmado
    # (estado inicial, nunca DEFAULT false — ver DECISÃO completa em
    # 043_coparticipation_confirmation.sql), FALSE = confirmado que NÃO
    # foi recebido (vazamento de receita provado).
    coparticipation_received: Mapped[bool | None] = mapped_column(Boolean)
    coparticipation_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    coparticipation_confirmed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("core.users.id"))
    # Épico F2.3 do Plano Diretor ("Auditoria documental leve — prontuário
    # × conta"). Versão RESTRITA (sem NLP semântico): confirmação de que
    # existe registro de prescrição/evolução sustentando este item OPME
    # (item_type == 'material_opme'). NULL = ainda não conferido (estado
    # inicial, nunca DEFAULT false — mesmo princípio de
    # coparticipation_received acima, ver DECISÃO completa em
    # 044_opme_documentation_confirmation.sql), FALSE = conferido e o
    # registro NÃO foi encontrado (risco de glosa documental provado).
    clinical_documentation_confirmed: Mapped[bool | None] = mapped_column(Boolean)
    clinical_documentation_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    clinical_documentation_confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.users.id")
    )
    # "Mapa de Dados Insighta" — Domínio Financeiro particular (Onda 1).
    # Ver PAYMENT_METHOD_VALUES acima e DECISÃO completa em
    # 049_billing_payment_method.sql.
    payment_method: Mapped[str | None] = mapped_column(String(20))
    installments: Mapped[int | None] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
