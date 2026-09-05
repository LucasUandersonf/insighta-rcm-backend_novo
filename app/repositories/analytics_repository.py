"""
app/repositories/analytics_repository.py

Agregações para os Dashboards de Decisão (Visão Geral, Agenda &
Capacidade, Insights Descritivos). Reaproveita o mesmo raciocínio já
documentado em reporting_repository.py e capacity_repository.py — soma/
conta no Postgres, nunca carrega linha por linha para a aplicação só
para descartar tudo, menos um número.

DECISÃO — `financial_hole_total` e `payment_gap_total` usam SQL cru com
LATERAL JOIN, não ORM
-------------------------------------------------------------------------
O briefing da Inteligência de Contratos pede DUAS divergências
separadas, nunca somadas na mesma métrica:

  1) `financial_hole_total` — "Divergência de Cobrança": quanto a
     CLÍNICA cobrou ABAIXO do valor contratado (vazamento de receita —
     ver `_rule_value_mismatch` em denial_risk_engine.py, que
     deliberadamente NÃO persiste esse valor em
     `value_saved_by_correction`, porque não foi "salvo", foi perdido).
     Compara `billing.charged_value` vs. `contract_items.agreed_price`.

  2) `payment_gap_total` — "Divergência de Recebimento": quanto a
     OPERADORA de fato PAGOU abaixo do que foi cobrado/contratado (ex:
     cobrou R$120, contrato previa R$140, operadora pagou R$90 -> R$50
     de buraco de recebimento). Só entra no cálculo billing já
     conciliado (`received_value IS NOT NULL` — `settle_billing` em
     billing_service.py é quem preenche essa coluna), senão estaríamos
     contando como "não recebido" um billing que simplesmente ainda não
     foi baixado.

Para agregar isso por período sem recalcular contrato-a-contrato em
Python (N+1 queries — inaceitável para uma agregação de dashboard, ao
contrário do fluxo de criação de UM billing, onde uma query extra é
irrelevante), usamos um LATERAL JOIN: para cada linha de billing, busca
o item de contrato vigente NA DATA daquele billing pelo código TUSS do
atendimento (mesma regra de vigência de
`ContractItemRepository.find_agreed_price`, replicada aqui em SQL,
juntando contract_items -> contracts). Session já é tenant-aware (SET
LOCAL aplicado) — RLS filtra billing/appointments/contracts/contract_items
pelo tenant normalmente, mesmo em SQL cru, porque roda na MESMA
conexão/transação da requisição.
"""
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.billing import Billing
from app.models.insurance_plan import InsurancePlan


def _bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(date_from, time.min, tzinfo=timezone.utc),
        datetime.combine(date_to, time.max, tzinfo=timezone.utc),
    )


class AnalyticsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def financial_hole_total(self, date_from: date, date_to: date) -> float:
        """Divergência de Cobrança: clínica cobrou (charged_value) abaixo
        do agreed_price do item de contrato vigente para o TUSS do
        atendimento."""
        start, end = _bounds(date_from, date_to)
        stmt = text(
            """
            SELECT COALESCE(SUM(GREATEST(ci.agreed_price - b.charged_value, 0)), 0)
            FROM core.billing b
            JOIN core.appointments a ON a.id = b.appointment_id
            LEFT JOIN LATERAL (
                SELECT it.agreed_price
                FROM core.contract_items it
                JOIN core.contracts c ON c.id = it.contract_id
                WHERE c.insurance_plan_id = b.insurance_plan_id
                  AND it.tuss_code = a.procedure_code
                  AND c.status = 'homologado'
                  AND c.valid_from <= b.created_at::date
                  AND (c.valid_until IS NULL OR c.valid_until >= b.created_at::date)
                ORDER BY c.valid_from DESC
                LIMIT 1
            ) ci ON true
            WHERE b.created_at >= :start AND b.created_at <= :end
            """
        )
        result = await self.session.execute(stmt, {"start": start, "end": end})
        return float(result.scalar_one())

    async def payment_gap_total(self, date_from: date, date_to: date) -> float:
        """Divergência de Recebimento: valor efetivamente PAGO pela
        operadora (billing.received_value, preenchido só após
        settle_billing) abaixo do agreed_price do item de contrato
        vigente. Só considera billings já conciliados — um billing sem
        received_value é "ainda não baixado", não "pago a menos"."""
        start, end = _bounds(date_from, date_to)
        stmt = text(
            """
            SELECT COALESCE(SUM(GREATEST(ci.agreed_price - b.received_value, 0)), 0)
            FROM core.billing b
            JOIN core.appointments a ON a.id = b.appointment_id
            LEFT JOIN LATERAL (
                SELECT it.agreed_price
                FROM core.contract_items it
                JOIN core.contracts c ON c.id = it.contract_id
                WHERE c.insurance_plan_id = b.insurance_plan_id
                  AND it.tuss_code = a.procedure_code
                  AND c.status = 'homologado'
                  AND c.valid_from <= b.created_at::date
                  AND (c.valid_until IS NULL OR c.valid_until >= b.created_at::date)
                ORDER BY c.valid_from DESC
                LIMIT 1
            ) ci ON true
            WHERE b.created_at >= :start AND b.created_at <= :end
              AND b.received_value IS NOT NULL
            """
        )
        result = await self.session.execute(stmt, {"start": start, "end": end})
        return float(result.scalar_one())

    async def financial_hole_by_plan(self, date_from: date, date_to: date) -> dict[str, float]:
        """Mesma regra de `financial_hole_total`, mas agrupada por
        convênio — a base do ranking de perda financeira por operadora
        (Painel → Faturamento). Um convênio some da lista quando não tem
        nenhum billing no período, não quando a soma dá zero — por isso
        o GROUP BY já resolve sozinho (sem convênio sem linha, sem
        entrada no dict)."""
        start, end = _bounds(date_from, date_to)
        stmt = text(
            """
            SELECT ip.display_name, COALESCE(SUM(GREATEST(ci.agreed_price - b.charged_value, 0)), 0)
            FROM core.billing b
            JOIN core.appointments a ON a.id = b.appointment_id
            JOIN core.insurance_plans ip ON ip.id = b.insurance_plan_id
            LEFT JOIN LATERAL (
                SELECT it.agreed_price
                FROM core.contract_items it
                JOIN core.contracts c ON c.id = it.contract_id
                WHERE c.insurance_plan_id = b.insurance_plan_id
                  AND it.tuss_code = a.procedure_code
                  AND c.status = 'homologado'
                  AND c.valid_from <= b.created_at::date
                  AND (c.valid_until IS NULL OR c.valid_until >= b.created_at::date)
                ORDER BY c.valid_from DESC
                LIMIT 1
            ) ci ON true
            WHERE b.created_at >= :start AND b.created_at <= :end
            GROUP BY ip.display_name
            """
        )
        result = await self.session.execute(stmt, {"start": start, "end": end})
        return {name: float(total) for name, total in result.all() if total}

    async def payment_gap_by_plan(self, date_from: date, date_to: date) -> dict[str, float]:
        """Mesma regra de `payment_gap_total`, agrupada por convênio."""
        start, end = _bounds(date_from, date_to)
        stmt = text(
            """
            SELECT ip.display_name, COALESCE(SUM(GREATEST(ci.agreed_price - b.received_value, 0)), 0)
            FROM core.billing b
            JOIN core.appointments a ON a.id = b.appointment_id
            JOIN core.insurance_plans ip ON ip.id = b.insurance_plan_id
            LEFT JOIN LATERAL (
                SELECT it.agreed_price
                FROM core.contract_items it
                JOIN core.contracts c ON c.id = it.contract_id
                WHERE c.insurance_plan_id = b.insurance_plan_id
                  AND it.tuss_code = a.procedure_code
                  AND c.status = 'homologado'
                  AND c.valid_from <= b.created_at::date
                  AND (c.valid_until IS NULL OR c.valid_until >= b.created_at::date)
                ORDER BY c.valid_from DESC
                LIMIT 1
            ) ci ON true
            WHERE b.created_at >= :start AND b.created_at <= :end
              AND b.received_value IS NOT NULL
            GROUP BY ip.display_name
            """
        )
        result = await self.session.execute(stmt, {"start": start, "end": end})
        return {name: float(total) for name, total in result.all() if total}

    async def denial_risk_value_by_plan(self, date_from: date, date_to: date) -> dict[str, float]:
        """Mesma regra de `denial_risk_value_breakdown` (valor faturado
        com denial_risk_level medium/high), agrupada por convênio em vez
        de por nível — o terceiro componente do ranking de perda por
        operadora, ao lado de buraco financeiro e divergência de
        recebimento."""
        start, end = _bounds(date_from, date_to)
        stmt = (
            select(InsurancePlan.display_name, func.coalesce(func.sum(Billing.charged_value), 0))
            .select_from(Billing)
            .join(InsurancePlan, InsurancePlan.id == Billing.insurance_plan_id)
            .where(
                Billing.created_at >= start,
                Billing.created_at <= end,
                Billing.denial_risk_level != "low",
            )
            .group_by(InsurancePlan.display_name)
        )
        result = await self.session.execute(stmt)
        return {name: float(total) for name, total in result.all() if total}

    async def contract_utilization(self, date_from: date, date_to: date) -> list[dict]:
        """
        Utilização de contrato: dos procedimentos NEGOCIADOS num contrato
        (contract_items), quantos foram de fato FATURADOS no período —
        o "buraco de utilização" é capacidade contratada parada, dinheiro
        que já foi negociado com o convênio e nunca vira receita porque a
        clínica simplesmente não fatura aquele procedimento.

        DECISÃO — `idle_catalog_value` é o VALOR DE TABELA dos itens
        parados, não uma estimativa de receita perdida
        -------------------------------------------------------------
        Diferente de `financial_hole_total`/`payment_gap_total` (que
        comparam um valor JÁ COBRADO contra o contratado), aqui não há
        nenhum billing do item para comparar — o procedimento simplesmente
        não foi faturado. Não há como estimar "quanto isso deveria ter
        faturado" sem inventar um volume hipotético, então o número
        reportado é o valor de tabela do item parado (quanto vale SE for
        faturado), não uma perda already-incorrida — o texto na tela
        precisa deixar isso explícito, mesmo cuidado de
        `financial_hole_total` vs. `payment_gap_total` nunca serem somados
        como se fossem a mesma coisa.

        Só contratos HOMOLOGADOS entram (mesmo filtro de
        `find_agreed_price`) — um contrato em rascunho/revisão não é
        "verdade" ainda, não faz sentido cobrar utilização dele.
        """
        start, end = _bounds(date_from, date_to)
        stmt = text(
            """
            SELECT
                c.id,
                ip.display_name,
                c.valid_from,
                c.valid_until,
                COUNT(ci.id) AS total_items,
                COUNT(DISTINCT CASE WHEN billed.tuss_code IS NOT NULL THEN ci.tuss_code END) AS items_billed,
                COALESCE(SUM(CASE WHEN billed.tuss_code IS NULL THEN ci.agreed_price ELSE 0 END), 0) AS idle_catalog_value
            FROM core.contracts c
            JOIN core.insurance_plans ip ON ip.id = c.insurance_plan_id
            JOIN core.contract_items ci ON ci.contract_id = c.id
            LEFT JOIN LATERAL (
                SELECT DISTINCT a.procedure_code AS tuss_code
                FROM core.billing b
                JOIN core.appointments a ON a.id = b.appointment_id
                WHERE b.insurance_plan_id = c.insurance_plan_id
                  AND a.procedure_code = ci.tuss_code
                  AND b.created_at >= :start AND b.created_at <= :end
                LIMIT 1
            ) billed ON true
            WHERE c.status = 'homologado'
            GROUP BY c.id, ip.display_name, c.valid_from, c.valid_until
            ORDER BY (COUNT(DISTINCT CASE WHEN billed.tuss_code IS NOT NULL THEN ci.tuss_code END)::float / NULLIF(COUNT(ci.id), 0)) ASC
            """
        )
        result = await self.session.execute(stmt, {"start": start, "end": end})
        return [
            {
                "contract_id": row[0],
                "plan_name": row[1],
                "valid_from": row[2],
                "valid_until": row[3],
                "total_items": row[4],
                "items_billed": row[5],
                "idle_catalog_value": float(row[6]),
            }
            for row in result.all()
        ]

    async def top_no_show_patients(
        self, date_from: date, date_to: date, *, min_sample: int = 3, limit: int = 10
    ) -> list[dict]:
        """
        "Lista vermelha" de pacientes: ranking por taxa de falta dentro da
        janela selecionada, entre atendimentos já OCORRIDOS (completed ou
        no_show — mesmo filtro de `no_show_risk_engine.assess`, cancelar
        com aviso não é o mesmo comportamento que faltar sem avisar).

        `min_sample` evita o mesmo problema estatístico documentado em
        `no_show_risk_engine.MIN_SPECIFIC_SAMPLES`: 1 falta em 1 consulta
        é 100% de taxa, mas não é um padrão — é ruído. Só entra no
        ranking quem tem pelo menos `min_sample` atendimentos no período
        E pelo menos 1 falta (paciente com 0 faltas não é "vermelho").
        """
        start, end = _bounds(date_from, date_to)
        stmt = text(
            """
            SELECT
                p.id,
                p.full_name,
                COUNT(*) FILTER (WHERE a.status = 'no_show') AS no_show_count,
                COUNT(*) AS total_appointments
            FROM core.appointments a
            JOIN core.patients p ON p.id = a.patient_id
            WHERE a.status IN ('completed', 'no_show')
              AND a.scheduled_at >= :start AND a.scheduled_at <= :end
            GROUP BY p.id, p.full_name
            HAVING COUNT(*) >= :min_sample AND COUNT(*) FILTER (WHERE a.status = 'no_show') > 0
            ORDER BY (COUNT(*) FILTER (WHERE a.status = 'no_show')::float / COUNT(*)) DESC, no_show_count DESC
            LIMIT :limit
            """
        )
        result = await self.session.execute(
            stmt, {"start": start, "end": end, "min_sample": min_sample, "limit": limit}
        )
        return [
            {
                "patient_id": row[0],
                "full_name": row[1],
                "no_show_count": row[2],
                "total_appointments": row[3],
                "no_show_rate": row[2] / row[3],
            }
            for row in result.all()
        ]

    async def all_patient_no_show_rates(self, *, min_sample: int = 3) -> list[float]:
        """
        Taxa de falta de CADA paciente com amostra suficiente
        (>= min_sample atendimentos resolvidos), SEM filtro de data e SEM
        exigir pelo menos 1 falta — diferente de `top_no_show_patients`
        (a lista vermelha de quem já é problema), aqui é a distribuição
        INTEIRA da clínica, inclusive pacientes com 0% de falta. Usado
        só para SUGERIR um limiar de risco calibrado com o histórico real
        desta clínica (ver no_show_risk_engine.suggest_thresholds) — não
        alimenta nenhum dashboard.

        Sem filtro de período de propósito: calibrar limiar com o
        histórico INTEIRO da clínica é mais estável estatisticamente do
        que uma janela recente pequena.
        """
        stmt = text(
            """
            SELECT COUNT(*) FILTER (WHERE a.status = 'no_show')::float / COUNT(*) AS rate
            FROM core.appointments a
            WHERE a.status IN ('completed', 'no_show')
            GROUP BY a.patient_id
            HAVING COUNT(*) >= :min_sample
            """
        )
        result = await self.session.execute(stmt, {"min_sample": min_sample})
        return [row[0] for row in result.all()]

    async def upcoming_risk_appointments(
        self,
        *,
        as_of: datetime,
        min_level: tuple[str, ...] = ("medio", "alto"),
        limit: int = 6,
        until: datetime | None = None,
    ) -> list[dict]:
        """
        Próximos agendamentos (status 'scheduled', ainda no futuro) com
        risco de falta médio ou alto, mais próximos primeiro — a "lista de
        atenção" da Sala de Comando ("Risco de falta — próximos dias" no
        canvas de design): diferente de `no_show_risk_breakdown`
        (contagem agregada por nível), aqui é a lista NOMINAL de quem
        precisa de uma ligação de confirmação esta semana.

        Não filtra por período (`date_from`/`date_to`) de propósito — é
        sempre "a partir de agora", igual a `DenialAppealRepository.
        count_due_within`: uma lista de ação não fica "vazia" só porque o
        gestor escolheu ver os últimos 7 dias no seletor do dashboard.

        `until` — teto OPCIONAL adicionado para o alerta diário de risco
        (app/worker/daily_alert_job.py), que quer só a janela das
        próximas 24h, não "todo o futuro" como o card do dashboard
        (chamador original, que nunca passa este parâmetro e continua
        com o comportamento de sempre).
        """
        stmt = text(
            """
            SELECT a.id, p.full_name, a.scheduled_at, a.no_show_risk_level
            FROM core.appointments a
            JOIN core.patients p ON p.id = a.patient_id
            WHERE a.status = 'scheduled'
              AND a.scheduled_at >= :as_of
              AND (CAST(:until AS timestamptz) IS NULL OR a.scheduled_at <= CAST(:until AS timestamptz))
              AND a.no_show_risk_level = ANY(:levels)
            ORDER BY a.scheduled_at ASC
            LIMIT :limit
            """
        )
        result = await self.session.execute(
            stmt, {"as_of": as_of, "until": until, "levels": list(min_level), "limit": limit}
        )
        return [
            {"appointment_id": row[0], "patient_full_name": row[1], "scheduled_at": row[2], "risk_level": row[3]}
            for row in result.all()
        ]

    async def avg_charged_value(self, date_from: date, date_to: date) -> float:
        start, end = _bounds(date_from, date_to)
        stmt = select(func.coalesce(func.avg(Billing.charged_value), 0)).where(
            Billing.created_at >= start, Billing.created_at <= end
        )
        return float((await self.session.execute(stmt)).scalar_one())

    async def denial_findings_by_plan(self, date_from: date, date_to: date) -> list[tuple[str, list[str]]]:
        """
        Retorna (nome_do_convenio, lista_de_reason_codes) por linha de
        billing com risco de glosa no período. A contagem POR motivo é
        feita em Python (ver smart_insights_engine.py) em vez de um
        UNNEST de JSONB em SQL — mesma decisão de "grade pequena -> lista
        em Python" de capacity_service.py: o volume de billings com risco
        num período de dashboard (dias/semanas) é pequeno o bastante para
        não justificar a complexidade de um LATERAL UNNEST só para contar
        strings dentro de um array JSONB.
        """
        start, end = _bounds(date_from, date_to)
        stmt = (
            select(InsurancePlan.display_name, Billing.denial_reasons)
            .select_from(Billing)
            .join(InsurancePlan, InsurancePlan.id == Billing.insurance_plan_id)
            .where(
                Billing.created_at >= start,
                Billing.created_at <= end,
                Billing.denial_risk_level != "low",
            )
        )
        result = await self.session.execute(stmt)
        return [(plan_name, reasons or []) for plan_name, reasons in result.all()]

    async def appointment_hour_histogram(self, date_from: date, date_to: date) -> dict[int, int]:
        """Horários de pico — para identificar em que faixa do dia a
        agenda mais lota (ex: "10h-11h é sistematicamente o pico")."""
        start, end = _bounds(date_from, date_to)
        hour_expr = func.extract("hour", Appointment.scheduled_at)
        stmt = (
            select(hour_expr, func.count())
            .where(
                Appointment.scheduled_at >= start,
                Appointment.scheduled_at <= end,
                Appointment.status != "cancelled",
            )
            .group_by(hour_expr)
        )
        result = await self.session.execute(stmt)
        return {int(hour): count for hour, count in result.all()}

    async def appointment_weekday_histogram(self, date_from: date, date_to: date) -> dict[int, int]:
        """Volume de agendamentos por DIA DA SEMANA (0=domingo..6=sábado —
        mesma convenção de capacity_service.py/no_show_risk_engine.py).
        Alimenta o insight textual "a agenda de segunda-feira caiu X%"
        (ver smart_insights_engine.py::_weekday_drop_insights) e o
        gráfico de apoio em Agenda & Capacidade.

        EXTRACT(DOW FROM timestamptz) do Postgres já devolve 0=domingo..
        6=sábado nativamente — ao contrário de Python weekday()
        (0=segunda), aqui NENHUMA conversão (current.weekday()+1)%7 é
        necessária. Mesmo filtro de status != 'cancelled' de
        appointment_hour_histogram: um agendamento cancelado não ocupou
        a agenda de fato, não deveria contar nem para "cheio" nem para
        "vazio" num dia específico."""
        start, end = _bounds(date_from, date_to)
        weekday_expr = func.extract("dow", Appointment.scheduled_at)
        stmt = (
            select(weekday_expr, func.count())
            .where(
                Appointment.scheduled_at >= start,
                Appointment.scheduled_at <= end,
                Appointment.status != "cancelled",
            )
            .group_by(weekday_expr)
        )
        result = await self.session.execute(stmt)
        return {int(weekday): count for weekday, count in result.all()}

    async def weekday_no_show_rate_breakdown(self, date_from: date, date_to: date) -> dict[int, tuple[int, int]]:
        """
        Taxa de falta por dia da semana — diferente de
        `appointment_weekday_histogram` (VOLUME bruto, inclui qualquer
        status não cancelado), aqui só entram atendimentos RESOLVIDOS
        (status 'completed' ou 'no_show' — mesmo filtro
        `_COMPLETED_OR_NO_SHOW` de no_show_risk_engine.py): um
        agendamento ainda 'scheduled' não tem desfecho conhecido, não
        deveria contar nem a favor nem contra a taxa de um dia
        específico, e um 'cancelled' é um comportamento diferente de
        faltar sem avisar (mesmo raciocínio já documentado no motor).

        Retorna {weekday: (no_show_count, total_relevante)} — a divisão
        (e a decisão de tratar total=0 como "sem amostra", não como
        0.0%) fica para o service, mesmo princípio de "None sobre zero"
        usado no resto do produto.
        """
        start, end = _bounds(date_from, date_to)
        weekday_expr = func.extract("dow", Appointment.scheduled_at)
        no_show_expr = func.sum(case((Appointment.status == "no_show", 1), else_=0))
        total_expr = func.count()
        stmt = (
            select(weekday_expr, no_show_expr, total_expr)
            .where(
                Appointment.scheduled_at >= start,
                Appointment.scheduled_at <= end,
                Appointment.status.in_(("completed", "no_show")),
            )
            .group_by(weekday_expr)
        )
        result = await self.session.execute(stmt)
        return {int(weekday): (int(no_show), int(total)) for weekday, no_show, total in result.all()}

    async def denial_risk_value_breakdown(self, date_from: date, date_to: date) -> dict[str, float]:
        """Soma de charged_value por denial_risk_level ('low'/'medium'/
        'high') faturado no período — alimenta o insight "X% do valor
        faturado está sob risco de glosa" (ver
        smart_insights_engine.py::_denial_risk_pct_insight). Value-based,
        não count-based, de propósito: uma diretoria se importa com QUANTO
        dinheiro está em risco, não com quantas linhas de billing —
        mesmo raciocínio de "impacto financeiro" já usado para ordenar o
        feed de insights inteiro."""
        start, end = _bounds(date_from, date_to)
        stmt = (
            select(Billing.denial_risk_level, func.coalesce(func.sum(Billing.charged_value), 0))
            .where(Billing.created_at >= start, Billing.created_at <= end)
            .group_by(Billing.denial_risk_level)
        )
        result = await self.session.execute(stmt)
        return {level: float(total) for level, total in result.all()}

    async def denial_risk_count_breakdown(self, date_from: date, date_to: date) -> dict[str, int]:
        """Mesmo agrupamento de `denial_risk_value_breakdown`, mas
        CONTANDO faturamentos em vez de somar valor — alimenta o donut
        "Distribuição de risco de glosa" do Painel (ver canvas de
        design, Painel.dc.html), onde o centro mostra "Revisados: N"
        (todo faturamento passa pelo motor anti-glosa na criação —
        denial_risk_engine.py roda de forma síncrona —, então "revisado"
        aqui é sinônimo de "faturado no período", não uma fila à parte)."""
        start, end = _bounds(date_from, date_to)
        stmt = (
            select(Billing.denial_risk_level, func.count())
            .where(Billing.created_at >= start, Billing.created_at <= end)
            .group_by(Billing.denial_risk_level)
        )
        result = await self.session.execute(stmt)
        return {level: int(count) for level, count in result.all()}

    async def no_show_risk_breakdown(self, *, as_of: datetime) -> dict[str, int]:
        """Agrupa AGENDAMENTOS FUTUROS AINDA NÃO REALIZADOS (status
        'scheduled') por nível de risco preditivo de falta — a mesma
        semântica de `upcoming_high_risk_appointments_count` em
        reporting_repository.py, mas com o detalhamento completo dos
        4 níveis (indeterminado/baixo/medio/alto), não só o alto.

        BUG CORRIGIDO (achado via scripts/seed_demo_data.py) — esta
        função recebia `date_from`/`date_to` e os usava para filtrar
        `scheduled_at`, mas os dois chamadores (AnalyticsService.
        get_agenda_metrics/get_smart_insights) sempre passam a janela do
        DASHBOARD (ex: "últimos 30 dias"), não uma janela futura. Como
        esta função só olha `status = 'scheduled'` (agendamento que
        ainda vai acontecer), a interseção com uma janela no PASSADO é
        estruturalmente vazia — o breakdown, `estimated_revenue_at_risk`
        e o insight de risco de falta nunca tinham o que mostrar em
        nenhum uso real do produto. Mesmo princípio já documentado (e já
        correto) em `upcoming_risk_appointments`, logo abaixo: não filtra
        por período do dashboard de propósito, sempre "a partir de
        agora" — resolvido aqui do mesmo jeito, com `as_of` explícito
        (não `datetime.now()` direto na query, para o service continuar
        podendo controlar/testar o relógio)."""
        level_expr = func.coalesce(Appointment.no_show_risk_level, "indeterminado")
        stmt = (
            select(level_expr, func.count())
            .where(Appointment.status == "scheduled", Appointment.scheduled_at >= as_of)
            .group_by(level_expr)
        )
        result = await self.session.execute(stmt)
        return {level: count for level, count in result.all()}

    async def ytd_billed_total(self, as_of: date) -> float:
        """Faturamento acumulado do ANO CALENDÁRIO até `as_of` (1º de
        janeiro do ano de `as_of` até o fim do dia de `as_of`) — alimenta
        o insight de desempenho anual vs. meta manual configurada em
        Minha Clínica (ver smart_insights_engine.py::_annual_goal_insight
        e Tenant.annual_revenue_goal). Deliberadamente INDEPENDENTE da
        janela de 7/14/30 dias do resto do dashboard — meta anual compara
        com o ano inteiro, não com a janela selecionada."""
        year_start = datetime(as_of.year, 1, 1, tzinfo=timezone.utc)
        _, end = _bounds(as_of, as_of)
        stmt = select(func.coalesce(func.sum(Billing.charged_value), 0)).where(
            Billing.created_at >= year_start, Billing.created_at <= end
        )
        return float((await self.session.execute(stmt)).scalar_one())

    async def inactive_patients_count(self, as_of: date, inactive_after_days: int = 365) -> int:
        """
        Conta pacientes que JÁ tiveram pelo menos um atendimento, mas cujo
        atendimento mais recente foi há mais de `inactive_after_days` dias
        (padrão 1 ano) — alimenta a recomendação de CRM/recuperação de
        pacientes inativos do insight de meta anual.

        DECISÃO — exige pelo menos 1 atendimento histórico
        -------------------------------------------------------------
        Um paciente sem NENHUM atendimento (cadastro sem histórico) não é
        "inativo", é um cadastro que talvez nunca tenha virado paciente de
        fato — misturar os dois inflaria a contagem e tornaria a
        recomendação de "recuperação" sem sentido (não há o que recuperar
        de alguém que nunca foi atendido). O INNER JOIN abaixo já exclui
        esses casos naturalmente (sem necessidade de um NOT EXISTS extra).
        """
        cutoff = datetime.combine(as_of - timedelta(days=inactive_after_days), time.min, tzinfo=timezone.utc)
        last_appointment = func.max(Appointment.scheduled_at)
        subq = select(Appointment.patient_id).group_by(Appointment.patient_id).having(last_appointment < cutoff)
        stmt = select(func.count()).select_from(subq.subquery())
        return int((await self.session.execute(stmt)).scalar_one())
