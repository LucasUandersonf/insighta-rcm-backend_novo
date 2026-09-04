"""
scripts/seed_demo_data.py

Gera um tenant de demonstração com dado sintético REALISTA. A maior
parte do fluxo passa pela API HTTP de verdade (não INSERT direto) —
o objetivo é exercitar a mesma lógica de negócio que um cliente real
dispara (motor de risco de glosa, motor de risco de falta, cálculo de
capacidade/ociosidade). A ÚNICA exceção é o volume de agendamentos
HISTÓRICOS (passados): não existe endpoint para transicionar o status
de uma consulta criada manualmente (scheduled -> completed/no_show)
— na aplicação real isso só acontece via ingestão de arquivo
(app/services/normalization_service.py grava o status junto). Replicar
o parser de CSV aqui só para popular histórico seria peso morto, então
o histórico entra direto no banco (bypass de RLS via role de
superusuário, mesmo princípio de tests/conftest.py::admin_engine) —
faturamento, recursos de glosa e a agenda FUTURA (a que importa para
risco preditivo) continuam 100% via API.

Uso:
    python -m scripts.seed_demo_data --base-url http://127.0.0.1:8010 \
        --admin-dsn postgresql://postgres:postgres@127.0.0.1:5432/insighta_demo

Não é idempotente de propósito: cada execução cria um tenant NOVO (CNPJ
aleatório) — rodar de novo não colide com uma execução anterior, só
soma outra clínica de demonstração ao mesmo banco.
"""
import argparse
import asyncio
import random
import string
import uuid
from datetime import date, datetime, timedelta, timezone

import asyncpg
import httpx

PROCEDURES = [
    ("10101012", "Consulta em consultório", 180.00),
    ("10104014", "Retorno em consultório", 90.00),
    ("20103019", "Fisioterapia — sessão", 95.00),
    ("40901018", "Ultrassonografia abdominal", 310.00),
    ("40301016", "Eletrocardiograma", 140.00),
    ("30602011", "Cirurgia — pequeno porte", 1240.00),
]

CID_CODES = ["Z00.0", "M54.5", "J06.9", "I10", "E11.9"]

PROFESSIONAL_NAMES = [
    ("Dra. Ana Beatriz Prado", "CRM-11223", "Clínico Geral"),
    ("Dr. Otávio Carvalho", "CRM-33441", "Cardiologia"),
    ("Dra. Fernanda Lacerda", "CRM-55667", "Ortopedia"),
    ("Dr. Ricardo Nunes", "CRM-77889", "Fisioterapia"),
    # Sem grade de propósito — simula o profissional que já entrou pela
    # ingestão de faturamento (achado F-02) mas nunca teve a grade
    # configurada: exatamente o buraco que a tela de Profissionais &
    # Agenda existe para fechar.
    ("Dra. Camila Rezende", None, "Dermatologia"),
]

FIRST_NAMES = [
    "Rafaela", "Bruno", "Larissa", "Diego", "Camila", "Felipe", "Juliana", "Marcelo",
    "Beatriz", "Gustavo", "Patrícia", "Rodrigo", "Vanessa", "Thiago", "Aline", "Eduardo",
    "Priscila", "André", "Renata", "Leonardo", "Débora", "Fábio", "Simone", "Vinícius",
    "Carolina", "Daniel", "Mariana", "Rafael", "Tatiane", "Lucas", "Amanda", "Paulo",
    "Cristiane", "Igor", "Letícia", "Rogério", "Natália", "Alexandre", "Bianca", "Márcio",
]
LAST_NAMES = [
    "Souza Martins", "Oliveira Costa", "Almeida Rocha", "Ferreira Lima", "Pereira Alves",
    "Ribeiro Santos", "Carvalho Dias", "Gomes Barbosa", "Mendes Cardoso", "Teixeira Nunes",
]

STAFF_ROLES = ["admin", "financeiro", "atendimento", "auditor"]

PLAN_CATALOG = [
    ("Unimed Nacional", "Empresarial"),
    ("Unimed Nacional", "Individual"),
    ("Bradesco Saúde", "Top Nacional"),
    ("SulAmérica", "Executivo"),
]


def _random_cnpj() -> str:
    return "".join(random.choices(string.digits, k=14))


def _now_suffix() -> str:
    return datetime.now().strftime("%H%M%S")


class DemoSeeder:
    def __init__(self, client: httpx.AsyncClient, admin_dsn: str):
        self.client = client
        self.admin_dsn = admin_dsn
        self.owner_headers: dict[str, str] = {}
        self.tenant_id: str = ""
        self.tenant_name = ""
        self.owner_email = ""
        self.owner_password = "SenhaDemo123!"

    async def _post(self, path: str, json: dict, headers: dict | None = None) -> dict:
        resp = await self.client.post(path, json=json, headers=headers or self.owner_headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"POST {path} -> {resp.status_code}: {resp.text}")
        return resp.json()

    async def _get(self, path: str, headers: dict | None = None) -> dict:
        resp = await self.client.get(path, headers=headers or self.owner_headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"GET {path} -> {resp.status_code}: {resp.text}")
        return resp.json()

    # -- Passo 1: tenant + owner + staff ---------------------------------

    async def register_tenant(self) -> None:
        suffix = _now_suffix()
        self.tenant_name = f"Clínica Vitalis Demo {suffix}"
        self.owner_email = f"marina.demo{suffix}@vitalis-demo.com.br"
        body = await self._post(
            "/api/v1/auth/register",
            {
                "trade_name": self.tenant_name,
                "cnpj": _random_cnpj(),
                "plan_tier": "professional",
                "owner_name": "Marina Costa",
                "email": self.owner_email,
                "password": self.owner_password,
            },
            headers={},
        )
        self.owner_headers = {"Authorization": f"Bearer {body['access_token']}"}
        tenant = await self._get("/api/v1/tenant")
        self.tenant_id = tenant["id"]
        print(f"✓ Tenant criado: {self.tenant_name} ({self.tenant_id})")
        print(f"  owner: {self.owner_email} / {self.owner_password}")

    async def set_annual_goal(self) -> None:
        resp = await self.client.patch(
            "/api/v1/tenant", json={"annual_revenue_goal": 480000.00}, headers=self.owner_headers
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"PATCH /api/v1/tenant -> {resp.status_code}: {resp.text}")
        print("✓ Meta de faturamento anual configurada (R$ 480.000,00)")

    async def create_staff_users(self) -> None:
        suffix = _now_suffix()
        for role in STAFF_ROLES:
            email = f"{role}.demo{suffix}@vitalis-demo.com.br"
            await self._post("/api/v1/users", {"email": email, "full_name": f"Usuário {role.title()} Demo", "role": role})
        print(f"✓ {len(STAFF_ROLES)} usuários de staff criados (owner + {', '.join(STAFF_ROLES)})")

    # -- Passo 2: convênios, planos, contratos ---------------------------

    async def create_plans_and_contracts(self) -> dict[str, list[tuple[str, float]]]:
        companies: dict[str, str] = {}
        plan_items: dict[str, list[tuple[str, float]]] = {}

        for company_name, plan_suffix in PLAN_CATALOG:
            if company_name not in companies:
                company = await self._post(
                    "/api/v1/insurance-companies", {"name": company_name, "default_appeal_deadline_days": 30}
                )
                companies[company_name] = company["id"]

            plan = await self._post(
                "/api/v1/insurance-companies/plans",
                {"insurance_company_id": companies[company_name], "display_name": f"{company_name} — {plan_suffix}"},
            )
            plan_id = plan["id"]

            items = [
                {"tuss_code": code, "procedure_name": name, "agreed_price": round(price * random.uniform(0.92, 1.08), 2)}
                for code, name, price in PROCEDURES
            ]
            await self._post(
                "/api/v1/contracts", {"insurance_plan_id": plan_id, "valid_from": "2026-01-01", "items": items}
            )
            plan_items[plan_id] = [(i["tuss_code"], i["agreed_price"]) for i in items]

        print(f"✓ {len(companies)} operadora(s), {len(plan_items)} plano(s), todos com contrato homologado")
        return plan_items

    # -- Passo 3: profissionais -------------------------------------------

    async def create_professionals(self) -> list[dict]:
        professionals = []
        weekday_grid = [{"weekday": wd, "start_time": "08:00:00", "end_time": "12:00:00"} for wd in range(1, 6)] + [
            {"weekday": wd, "start_time": "14:00:00", "end_time": "18:00:00"} for wd in range(1, 6)
        ]
        for full_name, registry, specialty in PROFESSIONAL_NAMES:
            availability = [] if registry is None else weekday_grid
            payload = {"full_name": full_name, "specialty": specialty, "availability": availability}
            if registry:
                payload["professional_registry"] = registry
            prof = await self._post("/api/v1/professionals", payload)
            professionals.append(prof)
        print(f"✓ {len(professionals)} profissionais cadastrados (1 sem grade, de propósito — ver ProfessionalsPage)")
        return professionals

    # -- Passo 4: pacientes ------------------------------------------------

    async def create_patients(self, count: int) -> list[dict]:
        patients = []
        used_names: set[str] = set()
        while len(patients) < count:
            name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
            if name in used_names:
                continue
            used_names.add(name)
            patient = await self._post("/api/v1/patients", {"full_name": name})
            patients.append(patient)
        print(f"✓ {len(patients)} pacientes cadastrados")
        return patients

    # -- Passo 5a: histórico (direto no banco — ver docstring do módulo) --

    async def seed_appointment_history(
        self,
        *,
        professionals: list[dict],
        patients: list[dict],
        plan_ids: list[str],
        history_days: int,
        problem_patient_ids: set[str],
    ) -> list[dict]:
        """Insere agendamentos PASSADOS direto no banco (bypass de RLS via
        superusuário) com status já resolvido (completed/no_show/cancelled)
        — ver docstring do módulo sobre por que não passa pela API. Só o
        que os motores de risco/capacidade precisam LER depois (status,
        scheduled_at, professional_id, duration_minutes) é populado; os
        campos que só fazem sentido calculados NA CRIAÇÃO via API
        (no_show_risk_level/score) ficam NULL — não influenciam nada
        histórico, só a exibição de um agendamento individual."""
        rows: list[dict] = []
        today = date.today()
        conn = await asyncpg.connect(self.admin_dsn)
        try:
            for day_offset in range(history_days, 0, -1):
                day = today - timedelta(days=day_offset)
                if day.weekday() >= 5:
                    continue
                for _ in range(random.randint(2, 5)):  # deliberadamente abaixo da capacidade instalada
                    professional = random.choice(professionals)
                    patient = random.choice(patients)
                    plan_id = random.choice(plan_ids)
                    procedure_code, _, _ = random.choice(PROCEDURES)
                    hour = random.choice([8, 9, 10, 11, 14, 15, 16, 17])
                    scheduled_at = datetime(day.year, day.month, day.day, hour, random.choice([0, 20, 40]), tzinfo=timezone.utc)

                    is_problem = patient["id"] in problem_patient_ids
                    roll = random.random()
                    if is_problem:
                        status = "no_show" if roll < 0.55 else "completed"
                    else:
                        status = "no_show" if roll < 0.08 else ("cancelled" if roll < 0.13 else "completed")

                    appt_id = str(uuid.uuid4())
                    duration = random.choice([30, 40, 50])
                    await conn.execute(
                        """
                        INSERT INTO core.appointments
                            (id, tenant_id, patient_id, insurance_plan_id, professional_id,
                             scheduled_at, duration_minutes, status, procedure_code, cid_code)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                        """,
                        appt_id,
                        self.tenant_id,
                        patient["id"],
                        plan_id,
                        professional["id"],
                        scheduled_at,
                        duration,
                        status,
                        procedure_code,
                        random.choice(CID_CODES) if random.random() > 0.1 else None,
                    )
                    rows.append(
                        {
                            "id": appt_id,
                            "_plan_id": plan_id,
                            "_procedure_code": procedure_code,
                            "status": status,
                        }
                    )
        finally:
            await conn.close()
        print(f"✓ {len(rows)} agendamentos históricos inseridos ({history_days} dias corridos, dias úteis)")
        return rows

    # -- Passo 5b: faturamento dos concluídos (via API — motor de glosa real) --

    async def bill_completed_appointments(
        self, history_rows: list[dict], plan_items: dict[str, list[tuple[str, float]]]
    ) -> list[str]:
        """Fatura (via API, exercitando BillingService/denial_risk_engine
        de verdade) os agendamentos históricos concluídos. Devolve os ids
        de faturamento com risco médio/alto — candidatos a recurso de
        glosa no passo seguinte."""
        risky_billing_ids: list[str] = []
        billed = 0
        for appt in history_rows:
            if appt["status"] != "completed":
                continue
            plan_id = appt["_plan_id"]
            procedure_code = appt["_procedure_code"]
            contract_price = next((price for code, price in plan_items.get(plan_id, []) if code == procedure_code), None)
            if contract_price is None:
                contract_price = next((p for code, _, p in PROCEDURES if code == procedure_code), 150.0)

            roll = random.random()
            if roll < 0.65:
                charged = contract_price
            elif roll < 0.85:
                charged = round(contract_price * random.uniform(0.6, 0.9), 2)
            else:
                charged = round(contract_price * random.uniform(1.1, 1.6), 2)

            billing = await self._post(
                "/api/v1/billing", {"appointment_id": appt["id"], "insurance_plan_id": plan_id, "charged_value": charged}
            )
            billed += 1
            if billing["denial_risk_level"] in ("medium", "high"):
                risky_billing_ids.append(billing["id"])

            if random.random() < 0.4:  # ~40% já liquidados pela operadora
                received = charged if random.random() < 0.6 else round(charged * random.uniform(0.75, 0.95), 2)
                await self.client.post(
                    f"/api/v1/billing/{billing['id']}/settle", json={"received_value": received}, headers=self.owner_headers
                )
        print(f"✓ {billed} faturamento(s) gerado(s) via API ({len(risky_billing_ids)} com risco médio/alto)")
        return risky_billing_ids

    # -- Passo 5c: agenda futura (via API — motor de no-show real) ---------

    async def create_future_appointments(
        self,
        *,
        professionals: list[dict],
        patients: list[dict],
        plan_ids: list[str],
        future_days: int,
        problem_patient_ids: set[str],
    ) -> list[dict]:
        appointments = []
        today = date.today()
        problem_list = list(problem_patient_ids)
        for day_offset in range(1, future_days + 1):
            day = today + timedelta(days=day_offset)
            if day.weekday() >= 5:
                continue
            for _ in range(random.randint(1, 3)):
                professional = random.choice(professionals)
                hour = random.choice([9, 10, 14, 16])
                scheduled_at = datetime(day.year, day.month, day.day, hour, 0, tzinfo=timezone.utc)
                use_problem_patient = problem_list and random.random() < 0.4
                patient = next((p for p in patients if p["id"] == random.choice(problem_list)), None) if use_problem_patient else random.choice(patients)
                if patient is None:
                    patient = random.choice(patients)
                procedure_code, _, _ = random.choice(PROCEDURES)
                appt = await self._post(
                    "/api/v1/appointments",
                    {
                        "patient_id": patient["id"],
                        "insurance_plan_id": random.choice(plan_ids),
                        "professional_id": professional["id"],
                        "scheduled_at": scheduled_at.isoformat(),
                        "duration_minutes": 40,
                        "procedure_code": procedure_code,
                    },
                )
                appointments.append(appt)
        print(f"✓ {len(appointments)} agendamentos futuros criados via API (risco de falta calculado ao vivo)")
        return appointments

    # -- Passo 6: recursos de glosa -----------------------------------------

    async def create_denial_appeals(self, billing_ids: list[str], limit: int = 4) -> None:
        statuses_flow = ["aberto", "protocolado", "deferido", "indeferido"]
        created = 0
        for billing_id in billing_ids[:limit]:
            denied_at = date.today() - timedelta(days=random.randint(5, 25))
            appeal = await self._post(
                "/api/v1/denial-appeals",
                {
                    "billing_id": billing_id,
                    "appeal_type": random.choice(["tecnica", "administrativa"]),
                    "operator_denial_reason": "Cobrança acima do valor de tabela — glosa administrativa.",
                    "denied_at": denied_at.isoformat(),
                },
            )
            target_status = random.choice(statuses_flow)
            if target_status in ("protocolado", "deferido", "indeferido"):
                await self._post(f"/api/v1/denial-appeals/{appeal['id']}/file", {})
            if target_status in ("deferido", "indeferido"):
                await self._post(
                    f"/api/v1/denial-appeals/{appeal['id']}/resolve",
                    {"status": target_status, "resolution_notes": "Resolução simulada para demonstração."},
                )
            created += 1
        print(f"✓ {created} recurso(s) de glosa aberto(s), em estágios variados")

    async def create_report_recipients(self) -> None:
        await self._post(
            "/api/v1/report-recipients",
            {"name": "Marina Costa", "phone_whatsapp": "+5511984321190", "email": self.owner_email, "report_types": []},
        )
        await self._post(
            "/api/v1/report-recipients",
            {"name": "Tiago Menezes (Financeiro)", "phone_whatsapp": "+5511977104482", "report_types": ["resumo_semanal"]},
        )
        print("✓ 2 destinatários de relatório cadastrados")

    # -- Passo final: validação via os próprios Dashboards de Decisão ------

    async def print_dashboard_summary(self) -> None:
        today = date.today()
        date_from = (today - timedelta(days=30)).isoformat()
        date_to = today.isoformat()

        summary = await self._get(f"/api/v1/analytics/executive-summary?date_from={date_from}&date_to={date_to}")
        agenda = await self._get(f"/api/v1/analytics/agenda-metrics?date_from={date_from}&date_to={date_to}")
        insights = await self._get(f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}")
        plan_ranking = await self._get(f"/api/v1/analytics/plan-loss-ranking?date_from={date_from}&date_to={date_to}")

        print("\n" + "=" * 72)
        print(f"RESUMO — {self.tenant_name}  (janela: {date_from} a {date_to})")
        print("=" * 72)
        print(f"Total faturado:              R$ {summary['total_billed']['value']:,.2f}")
        print(f"Buraco financeiro:           R$ {summary['financial_hole']['value']:,.2f}")
        print(f"Divergência de recebimento:  R$ {summary['payment_gap']['value']:,.2f}")
        print(f"Faturamento de alto risco:   {summary['high_risk_pending_count']} pendente(s)")
        util = summary.get("avg_capacity_utilization")
        print(f"Ocupação média da agenda:    {util['value']*100:.0f}%" if util else "Ocupação média da agenda:    sem dado suficiente")
        print(
            f"Ociosidade estimada em R$:   R$ {agenda['estimated_revenue_lost_to_idle_capacity']:,.2f} "
            f"({agenda['total_idle_minutes']} min ociosos)"
        )
        print(f"Receita em risco (no-show):  R$ {agenda['estimated_revenue_at_risk']:,.2f}")
        print(f"Lista vermelha (pacientes):  {len(agenda['patient_no_show_ranking'])} paciente(s)")
        print(f"Próximos agendamentos risco: {len(agenda['upcoming_risk_appointments'])}")
        print(f"Ranking de perda por plano:  {len(plan_ranking['plans'])} plano(s) com perda > 0")
        print(f"Insights automáticos:        {len(insights['insights'])} gerado(s)")
        for insight in insights["insights"][:6]:
            impact = f" (R$ {insight['financial_impact']:,.2f})" if insight.get("financial_impact") else ""
            print(f"  [{insight['severity']}] {insight['title']}{impact}")
        print("=" * 72)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Gera um tenant de demonstração com dado sintético realista.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--admin-dsn", default="postgresql://postgres:postgres@127.0.0.1:5432/insighta_demo")
    parser.add_argument("--patients", type=int, default=40)
    parser.add_argument("--history-days", type=int, default=90)
    parser.add_argument("--future-days", type=int, default=10)
    args = parser.parse_args()

    async with httpx.AsyncClient(base_url=args.base_url, timeout=30.0) as client:
        seeder = DemoSeeder(client, args.admin_dsn)
        await seeder.register_tenant()
        await seeder.set_annual_goal()
        await seeder.create_staff_users()
        plan_items = await seeder.create_plans_and_contracts()
        plan_ids = list(plan_items.keys())
        professionals = await seeder.create_professionals()
        patients = await seeder.create_patients(args.patients)
        problem_patient_ids = {p["id"] for p in random.sample(patients, k=min(5, len(patients)))}

        history_rows = await seeder.seed_appointment_history(
            professionals=professionals,
            patients=patients,
            plan_ids=plan_ids,
            history_days=args.history_days,
            problem_patient_ids=problem_patient_ids,
        )
        risky_billing_ids = await seeder.bill_completed_appointments(history_rows, plan_items)
        await seeder.create_future_appointments(
            professionals=professionals,
            patients=patients,
            plan_ids=plan_ids,
            future_days=args.future_days,
            problem_patient_ids=problem_patient_ids,
        )
        await seeder.create_denial_appeals(risky_billing_ids)
        await seeder.create_report_recipients()
        await seeder.print_dashboard_summary()


if __name__ == "__main__":
    asyncio.run(main())
