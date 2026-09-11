import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Vocabulário FECHADO e universal entre clínicas (confirmado idêntico
# em 3 ERPs do mercado — Moderna, Feegow, iClinic: "Amb/Int/PS" aparece
# como filtro/coluna em praticamente toda tela de faturamento
# pesquisada) — mesmo raciocínio de GUIA_TIPOS em app/models/guia.py.
TIPO_PACIENTE_VALUES = ("ambulatorial", "internacao", "pronto_socorro")

# Funil padrão de qualquer clínica (achado do Dicionário de Dados) — sem
# isso, não há como medir taxa de retorno nem separar "primeira consulta"
# de "retorno" em nenhum relatório.
VISIT_TYPE_VALUES = ("primeira_consulta", "retorno")


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        CheckConstraint(f"visit_type IS NULL OR visit_type IN {VISIT_TYPE_VALUES}", name="appointments_visit_type_check"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.patients.id"), nullable=False)
    insurance_plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("core.insurance_plans.id"))
    professional_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("core.professionals.id"))
    # Local de Atendimento/Unidade/Setor (ver app/models/local.py) e
    # Tipo de Paciente (Fase 4) — NULLABLE: todo agendamento existente
    # antes desta migration, e todo vindo da ingestão em massa hoje,
    # não tem essa informação (ver DECISÃO em
    # app/sql/018_locais_tipo_paciente.sql).
    local_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("core.locais.id"))
    tipo_paciente: Mapped[str | None] = mapped_column(String(20))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="scheduled")
    procedure_code: Mapped[str | None] = mapped_column(String(20))
    # Ausência de CID é um dos gatilhos do motor de risco de glosa (Etapa 3)
    cid_code: Mapped[str | None] = mapped_column(String(10))
    # Calculado na criação do agendamento por app/services/no_show_risk_engine.py
    # a partir do histórico do próprio paciente — não é dado que o cliente envia.
    no_show_risk_level: Mapped[str | None] = mapped_column(String(20))
    no_show_risk_score: Mapped[float | None] = mapped_column(Numeric(5, 4))
    # Identificador do agendamento NO SISTEMA DE ORIGEM (ex: "codigo_agendamento"
    # de um relatório de Agenda exportado pelo ERP) — Template de Integração
    # "Agenda" (ver app/sql/019_agenda_ingestion.sql). Chave de UPSERT: um
    # mesmo agendamento normalmente é reportado várias vezes ao longo do
    # tempo (nasce "scheduled", depois o mesmo arquivo/relatório reaparece
    # já com status "completed"/"no_show") — sem uma chave estável, cada
    # reimportação criaria um agendamento DUPLICADO em vez de atualizar o
    # existente. NULLABLE porque nem todo agendamento nasce da ingestão de
    # Agenda (criação manual via POST /appointments, e todo agendamento
    # vindo do template de Faturamento, nunca têm essa chave).
    external_id: Mapped[str | None] = mapped_column(String(100))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("core.users.id"))
    # Quando o agendamento foi de fato MARCADO no sistema de origem —
    # diferente de `created_at` abaixo, que é quando ESTA LINHA entrou no
    # nosso banco (poderia ser dias/semanas depois, numa reimportação em
    # lote). Sem isso, "tempo de espera até a consulta" não existe como
    # relatório (achado do Dicionário de Dados). NULLABLE: nem todo ERP
    # exporta essa data separada da data da própria consulta.
    booked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Primeira consulta ou retorno (ver VISIT_TYPE_VALUES) — NULLABLE
    # porque nem todo ERP de origem distingue isso hoje.
    visit_type: Mapped[str | None] = mapped_column(String(20))
    # Texto livre do motivo de cancelamento/remarcação — hoje o sistema
    # só sabe QUE um agendamento foi cancelado (status), nunca POR QUÊ
    # (achado do Dicionário de Dados). NULLABLE.
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    # Canal por onde ESTE agendamento específico foi marcado (telefone/
    # whatsapp/site/presencial) — DECISÃO: campo próprio, não reaproveita
    # Patient.acquisition_source (que existe, mas mede a origem de
    # MARKETING do paciente na primeira vez, um conceito diferente de
    # "por qual canal esta consulta específica foi marcada"; reaproveitar
    # sobrescreveria um dado de ROI de marketing com um dado operacional
    # toda vez que o mesmo paciente reagendasse por outro canal).
    booking_channel: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
