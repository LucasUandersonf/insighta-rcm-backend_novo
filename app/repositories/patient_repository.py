"""Mesmo padrão de billing_repository.py: sem WHERE tenant_id manual — o
RLS, sob a sessão tenant-aware injetada pelo endpoint, já garante isso."""
import uuid

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient


class PatientRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self, limit: int = 50, offset: int = 0) -> list[Patient]:
        stmt = select(Patient).order_by(Patient.full_name).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_all(self) -> int:
        stmt = select(func.count()).select_from(Patient)
        return (await self.session.execute(stmt)).scalar_one()

    async def get_by_id(self, patient_id: uuid.UUID) -> Patient | None:
        stmt = select(Patient).where(Patient.id == patient_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_cpf(self, cpf: str) -> Patient | None:
        stmt = select(Patient).where(Patient.cpf == cpf)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def add(self, patient: Patient) -> Patient:
        self.session.add(patient)
        await self.session.flush()
        return patient

    async def save(self, patient: Patient) -> Patient:
        await self.session.flush()
        return patient

    async def search(self, query: str, *, limit: int = 20) -> list[Patient]:
        """Busca por nome/CPF — Ficha do Paciente (Roadmap "Rumo à Nota
        9", Fase 4), mesmo padrão de BillingRepository.search (nome
        parcial via ILIKE, CPF por igualdade exata dos dígitos)."""
        digits = "".join(ch for ch in query if ch.isdigit())
        stmt = (
            select(Patient)
            .where(
                or_(
                    Patient.full_name.ilike(f"%{query}%"),
                    (Patient.cpf == digits) if digits else False,
                )
            )
            .order_by(Patient.full_name)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_ficha_appointments(self, patient_id: uuid.UUID, *, limit: int = 50) -> list[dict]:
        """
        Ficha do Paciente (Roadmap "Rumo à Nota 9", Fase 4) — pedido
        direto do usuário: "podemos juntar dados de pessoa física, com os
        dados de agendamento, com os dados de atendimento... cada conta
        possui um registro depois da abertura de atendimento". Uma linha
        por (atendimento, billing) — vários billings do mesmo atendimento
        (SADT com múltiplos procedimentos) viram várias linhas aqui, que
        o service reagrupa por atendimento (ver
        PatientService.get_ficha). LEFT JOIN em tudo que é opcional
        (profissional, convênio, faturamento) — um atendimento sem
        nenhum billing ainda aparece, só sem billing_id.

        Mais recente primeiro, capado em `limit` atendimentos DISTINTOS
        (não linhas) — por isso a subquery: sem ela, um atendimento com
        3 billings contaria como 3 das `limit` linhas, encolhendo o
        histórico de verdade que aparece na ficha.
        """
        stmt = text(
            """
            WITH recent_appointments AS (
                SELECT id FROM core.appointments
                WHERE patient_id = :patient_id
                ORDER BY scheduled_at DESC
                LIMIT :limit
            )
            SELECT
                a.id, a.scheduled_at, a.status, a.no_show_risk_level,
                prof.full_name AS professional_name,
                ip.display_name AS insurance_plan_name,
                b.id AS billing_id, b.charged_value, b.status AS billing_status,
                b.denial_risk_level, b.created_at AS billing_created_at
            FROM core.appointments a
            JOIN recent_appointments ra ON ra.id = a.id
            LEFT JOIN core.professionals prof ON prof.id = a.professional_id
            LEFT JOIN core.insurance_plans ip ON ip.id = a.insurance_plan_id
            LEFT JOIN core.billing b ON b.appointment_id = a.id
            ORDER BY a.scheduled_at DESC, b.created_at ASC
            """
        )
        result = await self.session.execute(stmt, {"patient_id": patient_id, "limit": limit})
        return [dict(row._mapping) for row in result.all()]

    async def get_ficha_summary(self, patient_id: uuid.UUID) -> dict:
        """Números agregados de TODO o histórico do paciente (não só os
        `limit` atendimentos mais recentes que `get_ficha_appointments`
        devolve) — a ficha mostra "faltou 4 de 20 vezes" mesmo quando só
        os últimos 50 atendimentos aparecem listados."""
        stmt = text(
            """
            SELECT
                COUNT(*) AS total_appointments,
                COUNT(*) FILTER (WHERE a.status = 'no_show') AS no_show_count,
                COUNT(*) FILTER (WHERE a.status IN ('completed', 'no_show')) AS resolved_count,
                MAX(a.scheduled_at) FILTER (WHERE a.status = 'completed') AS last_visit_at,
                COALESCE((
                    SELECT SUM(b.charged_value) FROM core.billing b
                    JOIN core.appointments a2 ON a2.id = b.appointment_id
                    WHERE a2.patient_id = :patient_id
                ), 0) AS total_billed,
                COALESCE((
                    SELECT SUM(b.value_saved_by_correction) FROM core.billing b
                    JOIN core.appointments a2 ON a2.id = b.appointment_id
                    WHERE a2.patient_id = :patient_id
                ), 0) AS total_value_saved
            FROM core.appointments a
            WHERE a.patient_id = :patient_id
            """
        )
        result = await self.session.execute(stmt, {"patient_id": patient_id})
        return dict(result.mappings().one())
    async def vip_signals_for(self, patient_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[int, int]]:
        """(visit_count, referral_count) por paciente — insumo de
        patient_value_engine.compute_vip_status ("Equilíbrio Insighta",
        perna Cliente). `visit_count` conta atendimentos NÃO cancelados
        (mesmo critério de `_EARLY_CHURN_CTE` em analytics_repository.py);
        `referral_count` conta quantos OUTROS pacientes têm este como
        `referred_by_patient_id`.

        Batch por lista de ids explícita (não a base inteira do tenant)
        — a tela de pacientes já pagina (ver PatientService.
        list_patients_paginated), então só precisa dos sinais da PÁGINA
        atual, nunca de todo mundo de uma vez."""
        if not patient_ids:
            return {}

        from app.models.appointment import Appointment

        visit_stmt = (
            select(Appointment.patient_id, func.count())
            .where(Appointment.patient_id.in_(patient_ids), Appointment.status != "cancelled")
            .group_by(Appointment.patient_id)
        )
        visit_counts = {row[0]: row[1] for row in (await self.session.execute(visit_stmt)).all()}

        referral_stmt = (
            select(Patient.referred_by_patient_id, func.count())
            .where(Patient.referred_by_patient_id.in_(patient_ids))
            .group_by(Patient.referred_by_patient_id)
        )
        referral_counts = {row[0]: row[1] for row in (await self.session.execute(referral_stmt)).all()}

        return {pid: (visit_counts.get(pid, 0), referral_counts.get(pid, 0)) for pid in patient_ids}

    async def list_birthdays_in_month(self, month: int) -> list[Patient]:
        """
        Achado do Dossiê Insighta RCM — `Patient.birth_date` é capturado
        pela normalização (Template de Faturamento) desde sempre, mas
        nenhuma tela lista aniversariantes do mês (ação clássica de
        relacionamento/retenção de clínica). Anonimização LGPD (ver
        `anonymized_at`, DECISÃO em app/sql/022_patient_lgpd_erasure.sql)
        já zera `birth_date` do titular — o filtro IS NOT NULL abaixo
        já exclui esses pacientes automaticamente, sem checagem extra.

        Ordenado por DIA do mês (não por nome): é assim que o gestor usa
        a lista — "quem faz aniversário essa semana", não uma lista
        alfabética.
        """
        stmt = (
            select(Patient)
            .where(Patient.birth_date.is_not(None), func.extract("month", Patient.birth_date) == month)
            .order_by(func.extract("day", Patient.birth_date))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
