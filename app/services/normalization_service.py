"""
app/services/normalization_service.py

Etapa 2 do pipeline do briefing original: promove cada linha "crua" de
core.ingestion_raw_rows para as entidades de negócio de verdade —
Patient, Appointment e Billing — reaproveitando tudo que já existe:
PatientRepository, AppointmentRepository, ContractItemRepository,
BillingRepository/denial_risk_engine. Nenhuma regra de negócio nova é
inventada aqui — este service é essencialmente um ORQUESTRADOR que
resolve ambiguidade de dado sujo (convênio escrito de N formas, paciente
que já existe ou é novo) antes de entregar para o motor de risco que já
tínhamos.

DECISÃO — normalização roda NA MESMA sessão/transação do worker de
ingestão, não em um poller separado
-------------------------------------------------------------------------
Cogitei um segundo worker fazendo polling em
ingestion_raw_rows.status='pending_normalization'. Descartei por agora:
isso adicionaria uma segunda fila e uma segunda latência de rede para um
trabalho que, por linha, é só um punhado de SELECTs de lookup — rápido
o bastante para rodar inline, logo depois de gravar a landing zone,
dentro da MESMA sessão tenant-aware que o ingestion_worker já abriu. Se a
normalização crescer (ex: matching fuzzy chamando um serviço externo),
este é o ponto certo para extrair um worker/fila dedicados — a interface
pública (normalize_rows) não muda, só quem a chama.

DECISÃO — convênio não encontrado NÃO cria um insurance_plan novo sozinho
-------------------------------------------------------------------------
Autocriar um convênio a partir de um texto ambíguo do arquivo seria pior
do que deixar a linha pendente: um convênio duplicado ou mal cadastrado
contamina contracts, ROI e o próprio painel de glosas depois. A linha
fica rejected com motivo "unknown_insurance_plan" — fica para revisão
humana na tela de Setup, onde o produto já cadastra convênios e tabela
de preços.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timezone

from app.core.text_utils import slugify
from app.models.appointment import Appointment
from app.models.billing import Billing
from app.models.guia import Guia
from app.models.ingestion_raw_row import IngestionRawRow
from app.models.local import Local
from app.models.patient import Patient
from app.models.professional import Professional
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.billing_repository import BillingRepository
from app.repositories.contract_item_repository import ContractItemRepository
from app.repositories.guia_repository import GuiaRepository
from app.repositories.insurance_plan_repository import InsurancePlanRepository
from app.repositories.local_repository import LocalRepository
from app.repositories.patient_repository import PatientRepository
from app.repositories.professional_repository import ProfessionalRepository
from app.services.denial_risk_engine import assess
from app.worker.schemas import RawAppointmentRow, RawBillingRow


@dataclass
class NormalizationSummary:
    normalized: int = 0
    rejected: int = 0




class NormalizationService:
    def __init__(
        self,
        *,
        patient_repo: PatientRepository,
        professional_repo: ProfessionalRepository,
        appointment_repo: AppointmentRepository,
        contract_item_repo: ContractItemRepository,
        insurance_plan_repo: InsurancePlanRepository,
        billing_repo: BillingRepository,
        local_repo: LocalRepository,
        guia_repo: GuiaRepository,
    ):
        self.patient_repo = patient_repo
        self.professional_repo = professional_repo
        self.appointment_repo = appointment_repo
        self.contract_item_repo = contract_item_repo
        self.insurance_plan_repo = insurance_plan_repo
        self.billing_repo = billing_repo
        self.local_repo = local_repo
        self.guia_repo = guia_repo

    async def _get_or_create_patient(self, tenant_id: uuid.UUID, row: RawBillingRow | RawAppointmentRow) -> Patient:
        if row.patient_cpf:
            existing = await self.patient_repo.get_by_cpf(row.patient_cpf)
            if existing is not None:
                return existing
        return await self.patient_repo.add(
            Patient(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                full_name=row.patient_name,
                cpf=row.patient_cpf,
                birth_date=None,
            )
        )

    async def _get_or_create_professional(
        self, tenant_id: uuid.UUID, row: RawBillingRow | RawAppointmentRow
    ) -> Professional | None:
        """
        CORREÇÃO (Auditoria Go-Live, achado F-02) — antes desta mudança, o
        ÚNICO jeito de um Profissional existir era uma tela de CRUD manual
        (removida — ver /professionals no frontend), o que deixava a Agenda
        & Capacidade estruturalmente inviável para clínicas que só operam
        via ingestão de arquivo. Agora o profissional entra pelo mesmo
        caminho que o paciente: extraído da própria linha de faturamento.

        Retorna None quando a linha não trouxe nome de profissional — o
        atendimento é normalizado sem professional_id (mesmo comportamento
        de antes desta correção; a coluna já era nullable), não é um erro.
        """
        if not row.professional_name:
            return None

        if row.professional_registry:
            existing = await self.professional_repo.get_by_registry(row.professional_registry)
            if existing is not None:
                return existing
        else:
            # Sem registro profissional na linha: só o fallback por nome
            # exato está disponível (mesma limitação documentada em
            # ProfessionalRepository.get_by_name).
            existing = await self.professional_repo.get_by_name(row.professional_name)
            if existing is not None:
                return existing

        return await self.professional_repo.add(
            Professional(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                full_name=row.professional_name,
                professional_registry=row.professional_registry,
                specialty=None,
                is_active=True,
            )
        )

    async def _get_or_create_local(self, tenant_id: uuid.UUID, row: RawBillingRow | RawAppointmentRow) -> Local | None:
        """
        Coluna `local_atendimento` do template estendido (Fase de
        "Templates de Integração") — mesmo caminho de get-or-create de
        paciente/profissional. Diferente de convênio (nunca auto-criado —
        ver DECISÃO no topo do arquivo): um Local errado/duplicado não
        contamina preço/contrato nenhum, então o risco de auto-criar é
        baixo — o pior caso é um Local a mais na lista, corrigível a
        qualquer momento na tela de gestão (desativar/renomear).
        """
        if not row.local_name:
            return None
        existing = await self.local_repo.get_by_name(row.local_name)
        if existing is not None:
            return existing
        return await self.local_repo.add(Local(id=uuid.uuid4(), tenant_id=tenant_id, nome=row.local_name))

    async def _get_or_create_guia(self, tenant_id: uuid.UUID, row: RawBillingRow, insurance_plan_id: uuid.UUID) -> Guia | None:
        """
        Colunas `guia_tipo`/`guia_numero`/`guia_senha` do template
        estendido. Quando `guia_numero` vem preenchido, PROCURA uma guia
        já existente com esse número (mesmo convênio) antes de criar uma
        nova — é assim que várias linhas do mesmo arquivo (ex.: vários
        procedimentos de uma SADT) acabam agrupadas numa ÚNICA Guia,
        exatamente como no mundo real (ver DECISÃO em
        app/sql/015_billing_guia.sql sobre Guia 1:N Billing). Sem
        `guia_numero`, não há chave para agrupar — cada linha cria sua
        própria guia.
        """
        if not row.guia_tipo:
            return None
        if row.guia_numero:
            existing = await self.guia_repo.get_by_numero(insurance_plan_id, row.guia_numero)
            if existing is not None:
                return existing
        return await self.guia_repo.add(
            Guia(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                insurance_plan_id=insurance_plan_id,
                tipo=row.guia_tipo,
                numero=row.guia_numero,
                senha=row.guia_senha,
            )
        )

    async def normalize_row(self, tenant_id: uuid.UUID, raw_row: IngestionRawRow, source_file: str | None) -> bool:
        """Retorna True se a linha foi promovida com sucesso, False se ficou rejected."""
        if raw_row.status != "pending_normalization":
            return False  # já processada (rejected na validação estrutural da Etapa 1) ou já normalizada

        row = RawBillingRow.model_validate(raw_row.payload)

        plan = await self.insurance_plan_repo.resolve(row.insurance_plan_raw_name, slugify(row.insurance_plan_raw_name))
        if plan is None:
            raw_row.status = "rejected"
            raw_row.validation_errors = {
                "reason": "unknown_insurance_plan",
                "raw_value": row.insurance_plan_raw_name,
            }
            return False

        # Registra a variação para acelerar o match na próxima importação
        # (só grava se ainda não vista — ver record_alias_if_new).
        await self.insurance_plan_repo.record_alias_if_new(
            tenant_id=tenant_id, plan_id=plan.id, raw_name=row.insurance_plan_raw_name, source_file=source_file
        )

        patient = await self._get_or_create_patient(tenant_id, row)
        professional = await self._get_or_create_professional(tenant_id, row)
        local = await self._get_or_create_local(tenant_id, row)
        guia = await self._get_or_create_guia(tenant_id, row, plan.id)

        appointment = await self.appointment_repo.add(
            Appointment(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                patient_id=patient.id,
                insurance_plan_id=plan.id,
                professional_id=professional.id if professional is not None else None,
                local_id=local.id if local is not None else None,
                tipo_paciente=row.tipo_paciente,
                scheduled_at=datetime.combine(row.service_date, time.min, tzinfo=timezone.utc),
                status="completed",  # dado importado já é um atendimento realizado, não agendado
                procedure_code=row.procedure_code,
                cid_code=row.cid_code,
                created_by=None,  # sem usuário humano por trás — veio de importação automática
            )
        )

        # Mesmo motor de risco de glosa que os endpoints da API usam —
        # nenhuma lógica de scoring duplicada aqui.
        contract_item = await self.contract_item_repo.find_agreed_price(plan.id, row.procedure_code)
        risk = assess(appointment, contract_item, row.charged_value)

        await self.billing_repo.add(
            Billing(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                appointment_id=appointment.id,
                insurance_plan_id=plan.id,
                guia_id=guia.id if guia is not None else None,
                charged_value=row.charged_value,
                status="held_for_review" if risk.should_hold_for_review else "pending",
                denial_risk_level=risk.level,
                denial_reasons=risk.reasons,
                value_saved_by_correction=float(risk.value_saved_by_correction),
            )
        )

        raw_row.status = "normalized"
        return True

    async def normalize_rows(
        self, tenant_id: uuid.UUID, raw_rows: list[IngestionRawRow], source_file: str | None = None
    ) -> NormalizationSummary:
        """
        BUG CORRIGIDO (achado testando com upload real de arquivo) —
        `raw_rows` inclui TODA linha salva por `save_raw_rows`, inclusive
        as que a Etapa 1 (parsing estrutural) já rejeitou ANTES de
        chegarem aqui (status já é 'rejected', não 'pending_normalization'
        — ver IngestionRepository.save_raw_rows). Para essas,
        `normalize_row` retorna False sem tocar o status (early return),
        mas o `elif raw_row.status == "rejected"` abaixo enxergava o
        status HERDADO da Etapa 1 e contava a linha de novo em
        `summary.rejected` — dobrando a contagem dessas linhas em
        `error_row_count` (ver ingestion_processing_service.py:
        `error_row_count = structural_error_count + summary.rejected`,
        onde a mesma linha já entra em `structural_error_count`). Só
        conta aqui uma linha que ESTAVA pendente de normalização e virou
        'rejected' NESTA passada (unknown_insurance_plan) — a rejeição
        estrutural já está contabilizada em `structural_error_count`.
        """
        summary = NormalizationSummary()
        for raw_row in raw_rows:
            was_pending = raw_row.status == "pending_normalization"
            promoted = await self.normalize_row(tenant_id, raw_row, source_file)
            if promoted:
                summary.normalized += 1
            elif was_pending and raw_row.status == "rejected":
                summary.rejected += 1
        return summary

    async def _get_or_create_or_update_appointment_from_agenda(
        self, tenant_id: uuid.UUID, row: RawAppointmentRow, insurance_plan_id: uuid.UUID | None
    ) -> Appointment:
        """
        UPSERT por `external_id` (ver DECISÃO em
        app/sql/019_agenda_ingestion.sql): um relatório de Agenda
        tipicamente reexporta o MESMO agendamento várias vezes conforme
        seu status muda (agendado -> confirmado -> atendido/faltou) — sem
        casar pelo código do sistema de origem, cada reimportação criaria
        um agendamento duplicado em vez de atualizar o existente.

        Sem `external_id` na linha (limitação aceita e documentada, mesma
        classe das demais get-or-create deste service): sempre cria um
        agendamento novo, nunca tenta casar por outro critério — não há
        um substituto seguro (paciente+data não identifica um agendamento
        único quando o mesmo paciente tem duas consultas no mesmo dia).
        """
        patient = await self._get_or_create_patient(tenant_id, row)
        professional = await self._get_or_create_professional(tenant_id, row)
        local = await self._get_or_create_local(tenant_id, row)

        existing = await self.appointment_repo.get_by_external_id(tenant_id, row.external_id) if row.external_id else None
        if existing is not None:
            existing.patient_id = patient.id
            existing.insurance_plan_id = insurance_plan_id
            existing.professional_id = professional.id if professional is not None else None
            existing.local_id = local.id if local is not None else None
            existing.tipo_paciente = row.tipo_paciente
            existing.scheduled_at = row.scheduled_at
            existing.duration_minutes = row.duration_minutes
            existing.status = row.status
            existing.procedure_code = row.procedure_code
            existing.cid_code = row.cid_code
            return await self.appointment_repo.save(existing)

        return await self.appointment_repo.add(
            Appointment(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                patient_id=patient.id,
                insurance_plan_id=insurance_plan_id,
                professional_id=professional.id if professional is not None else None,
                local_id=local.id if local is not None else None,
                tipo_paciente=row.tipo_paciente,
                scheduled_at=row.scheduled_at,
                duration_minutes=row.duration_minutes,
                status=row.status,
                procedure_code=row.procedure_code,
                cid_code=row.cid_code,
                external_id=row.external_id,
                created_by=None,  # sem usuário humano por trás — veio de importação automática
            )
        )

    async def normalize_agenda_row(self, tenant_id: uuid.UUID, raw_row: IngestionRawRow, source_file: str | None) -> bool:
        """
        Equivalente a `normalize_row`, mas para o Template de Integração
        "Agenda": promove a linha crua para um Appointment (UPSERT — ver
        `_get_or_create_or_update_appointment_from_agenda`) e NUNCA cria
        Billing/Guia — uma linha de Agenda é um agendamento, não uma
        cobrança (ver docstring do módulo app/worker/schemas.py sobre a
        diferença entre os dois templates).
        """
        if raw_row.status != "pending_normalization":
            return False

        row = RawAppointmentRow.model_validate(raw_row.payload)

        plan_id: uuid.UUID | None = None
        if row.insurance_plan_raw_name:
            plan = await self.insurance_plan_repo.resolve(row.insurance_plan_raw_name, slugify(row.insurance_plan_raw_name))
            if plan is None:
                raw_row.status = "rejected"
                raw_row.validation_errors = {
                    "reason": "unknown_insurance_plan",
                    "raw_value": row.insurance_plan_raw_name,
                }
                return False
            plan_id = plan.id
            await self.insurance_plan_repo.record_alias_if_new(
                tenant_id=tenant_id, plan_id=plan.id, raw_name=row.insurance_plan_raw_name, source_file=source_file
            )

        await self._get_or_create_or_update_appointment_from_agenda(tenant_id, row, plan_id)

        raw_row.status = "normalized"
        return True

    async def normalize_agenda_rows(
        self, tenant_id: uuid.UUID, raw_rows: list[IngestionRawRow], source_file: str | None = None
    ) -> NormalizationSummary:
        """Equivalente a `normalize_rows`, chamando `normalize_agenda_row`
        por linha — mesmo cuidado de contagem documentado lá (uma linha
        já rejeitada na Etapa 1 não é contada de novo aqui)."""
        summary = NormalizationSummary()
        for raw_row in raw_rows:
            was_pending = raw_row.status == "pending_normalization"
            promoted = await self.normalize_agenda_row(tenant_id, raw_row, source_file)
            if promoted:
                summary.normalized += 1
            elif was_pending and raw_row.status == "rejected":
                summary.rejected += 1
        return summary

    async def resolve_unknown_insurance_plan(
        self,
        *,
        tenant_id: uuid.UUID,
        target_row: IngestionRawRow,
        insurance_plan_id: uuid.UUID,
        also_resolve_matching_rows: list[IngestionRawRow],
        source_file: str | None,
    ) -> "ResolutionSummary":
        """
        Fluxo da tela de Setup: um humano mapeia manualmente
        "UNIMED NAC." (texto que a normalização automática não reconheceu)
        para um insurance_plan_id que ele escolhe na interface.

        DECISÃO — registrar o alias ANTES de tentar promover de novo
        -------------------------------------------------------------
        Gravamos a variação em insurance_plan_aliases primeiro, e só
        DEPOIS voltamos o status da linha para 'pending_normalization' e
        chamamos normalize_row(). Assim reaproveitamos o MESMO caminho de
        resolução por alias que já existia (InsurancePlanRepository.resolve),
        em vez de ter um segundo caminho de código "promover direto" que
        pularia a lógica de registro de convênio/paciente/glosa já testada.

        DECISÃO — resolução em lote das demais linhas com o mesmo texto cru
        -------------------------------------------------------------
        Um arquivo diário de importação costuma repetir o mesmo convênio
        (mal escrito) várias vezes. Depois de mapear manualmente UMA vez,
        as outras linhas `rejected` com o mesmo `raw_value` já podem ser
        promovidas automaticamente — o alias que acabamos de gravar já
        resolve todas elas. Sem isso, o usuário teria que repetir o mesmo
        clique linha por linha.
        """
        target_row.status = "pending_normalization"
        raw_value = (target_row.validation_errors or {}).get("raw_value") or ""
        target_row.validation_errors = None

        # Grava o alias ANTES de tentar normalizar de novo — sem isso,
        # insurance_plan_repo.resolve() dentro de normalize_row() falharia
        # exatamente pelo mesmo motivo de antes (ver docstring acima).
        await self.insurance_plan_repo.record_alias_if_new(
            tenant_id=tenant_id, plan_id=insurance_plan_id, raw_name=raw_value, source_file=source_file
        )
        target_promoted = await self.normalize_row(tenant_id, target_row, source_file)

        additionally_resolved = 0
        for row in also_resolve_matching_rows:
            if row.id == target_row.id:
                continue  # já processada acima
            row.status = "pending_normalization"
            row.validation_errors = None
            if await self.normalize_row(tenant_id, row, source_file):
                additionally_resolved += 1

        return ResolutionSummary(target_resolved=target_promoted, additionally_resolved=additionally_resolved)


@dataclass
class ResolutionSummary:
    target_resolved: bool
    additionally_resolved: int
