"""
scripts/seed_existing_tenant_via_api.py

Variante de scripts/seed_demo_data.py para popular um tenant que JÁ
EXISTE (login de verdade, e-mail/senha reais) em vez de criar um tenant
novo — pedido explícito do usuário: "quero injetar dado no meu usuário"
pra ver a Sala de Comando com dado real na conta que ele já usa.

DECISÃO — 100% via API HTTP, ZERO acesso direto a banco
-------------------------------------------------------------------
seed_demo_data.py precisa de um DSN de superusuário Postgres porque,
quando foi escrito, não existia endpoint para transicionar o status de
um agendamento (scheduled -> completed/no_show/cancelled) — só a
ingestão de CSV gravava status resolvido direto. Isso mudou: hoje existe
PATCH /appointments/{id} (ver AppointmentUpdateRequest, app/schemas/
appointment.py) que aceita `status` para qualquer um dos 4 valores
conhecidos, em qualquer agendamento, sem precisar vir de arquivo. Este
script explora exatamente isso: cria o agendamento HISTÓRICO com
`scheduled_at` no passado via POST normal, e resolve o status logo
depois com PATCH — tudo através da mesma API que um usuário real usa,
nunca um INSERT direto. Isso também significa que este script roda
contra QUALQUER ambiente (inclusive produção) só com uma URL pública e
um login válido — não precisa da string de conexão do Postgres, que
nunca deveria estar em texto plano fora do Railway.

DECISÃO — idempotente por checagem, não por chave única
-------------------------------------------------------------------
Ao contrário de seed_demo_data.py (sempre cria um tenant novo, nunca
colide), este script escreve num tenant que já existe e pode já ter
dado real. Antes de criar qualquer operadora/plano/profissional/
paciente, verifica se algo com o mesmo nome já existe e reaproveita —
rodar duas vezes não duplica o cadastro base (só a agenda/faturamento
histórico, que é per-execução de propósito, para simular volume
crescente).

DECISÃO — sem usuários de staff nem destinatários de relatório
fictícios
-------------------------------------------------------------------
Diferente de seed_demo_data.py, este script NÃO cria usuários de
staff nem destinatários de relatório (WhatsApp/e-mail) fictícios: isto
é a conta REAL de alguém, criar logins ou apontar o relatório semanal
automático para um número de telefone inventado seria um efeito
colateral indesejado numa conta de verdade, diferente de um tenant de
demonstração descartável.

Uso:
    python3 scripts/seed_existing_tenant_via_api.py \
        --base-url https://insighta-rcm-backendnovo-production.up.railway.app \
        --email admin@insightarcm.com.br --password 'Insighta@2026!'

Só biblioteca padrão (urllib) de propósito — roda em qualquer ambiente
com Python 3, sem precisar instalar httpx/asyncpg antes.
"""
import argparse
import json
import random
import string
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

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
    # Sem grade de propósito — mesmo cenário de seed_demo_data.py: simula
    # o profissional que entrou pela ingestão de faturamento mas nunca
    # teve a grade configurada (achado F-02, ver ProfessionalsPage).
    ("Dra. Camila Rezende", None, "Dermatologia"),
]

FIRST_NAMES = [
    "Rafaela", "Bruno", "Larissa", "Diego", "Camila", "Felipe", "Juliana", "Marcelo",
    "Beatriz", "Gustavo", "Patrícia", "Rodrigo", "Vanessa", "Thiago", "Aline", "Eduardo",
    "Priscila", "André", "Renata", "Leonardo", "Débora", "Fábio", "Simone", "Vinícius",
    "Carolina", "Daniel", "Mariana", "Rafael", "Tatiane", "Lucas", "Amanda", "Paulo",
]
LAST_NAMES = [
    "Souza Martins", "Oliveira Costa", "Almeida Rocha", "Ferreira Lima", "Pereira Alves",
    "Ribeiro Santos", "Carvalho Dias", "Gomes Barbosa", "Mendes Cardoso", "Teixeira Nunes",
]

PLAN_CATALOG = [
    ("Unimed Nacional", "Empresarial"),
    ("Unimed Nacional", "Individual"),
    ("Bradesco Saúde", "Top Nacional"),
    ("SulAmérica", "Executivo"),
]


class ApiError(RuntimeError):
    pass


class Api:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.token: str | None = None

    def _request(self, method: str, path: str, payload: dict | None = None, auth: bool = True):
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if auth:
            if not self.token:
                raise ApiError("Chamada autenticada sem token — login() precisa rodar primeiro.")
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise ApiError(f"{method} {path} -> {e.code}: {detail}") from e

    def get(self, path: str):
        return self._request("GET", path)

    def post(self, path: str, payload: dict | None = None, auth: bool = True):
        return self._request("POST", path, payload, auth=auth)

    def patch(self, path: str, payload: dict):
        return self._request("PATCH", path, payload)

    def login(self, email: str, password: str) -> None:
        body = self.post("/api/v1/auth/login", {"email": email, "password": password}, auth=False)
        if body.get("requires_tenant_selection"):
            options = ", ".join(f"{o['trade_name']} ({o['tenant_id']})" for o in body["tenant_options"])
            raise ApiError(
                f"Este e-mail existe em mais de uma clínica — passe --tenant-id explicitamente. Opções: {options}"
            )
        self.token = body["access_token"]


def _random_suffix() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=6))


def get_or_create_insurance_companies_and_plans(api: Api) -> dict[str, list[tuple[str, float]]]:
    """Devolve {plan_id: [(tuss_code, agreed_price), ...]} — reaproveita
    operadora/plano/contrato já existentes com o MESMO nome (idempotente
    por nome, não por chave técnica) em vez de duplicar a cada execução."""
    existing_companies = {c["name"]: c["id"] for c in api.get("/api/v1/insurance-companies?include_inactive=true")}
    existing_plans = {p["display_name"]: p for p in api.get("/api/v1/insurance-companies/plans?include_inactive=true")}
    existing_contracts = api.get("/api/v1/contracts/active?limit=200&offset=0")["items"]
    plan_ids_with_contract = {c["insurance_plan_id"] for c in existing_contracts}

    plan_items: dict[str, list[tuple[str, float]]] = {}
    created_companies = created_plans = created_contracts = 0

    for company_name, plan_suffix in PLAN_CATALOG:
        company_id = existing_companies.get(company_name)
        if company_id is None:
            company = api.post("/api/v1/insurance-companies", {"name": company_name, "default_appeal_deadline_days": 30})
            company_id = company["id"]
            existing_companies[company_name] = company_id
            created_companies += 1

        display_name = f"{company_name} — {plan_suffix}"
        plan = existing_plans.get(display_name)
        if plan is None:
            plan = api.post(
                "/api/v1/insurance-companies/plans",
                {"insurance_company_id": company_id, "display_name": display_name},
            )
            existing_plans[display_name] = plan
            created_plans += 1
        plan_id = plan["id"]

        if plan_id in plan_ids_with_contract:
            # Já homologado — reconstrói a tabela de preço a partir do
            # próprio catálogo (preço determinístico, não lê de volta
            # o contrato existente porque a rota de leitura de item não
            # é necessária pra este script: usamos a MESMA lista de
            # PROCEDURES sempre, então os preços batem por construção).
            plan_items[plan_id] = [
                (code, round(price * 1.0, 2)) for code, _, price in PROCEDURES
            ]
            continue

        items = [
            {"tuss_code": code, "procedure_name": name, "agreed_price": round(price * random.uniform(0.92, 1.08), 2)}
            for code, name, price in PROCEDURES
        ]
        api.post("/api/v1/contracts", {"insurance_plan_id": plan_id, "valid_from": "2026-01-01", "items": items})
        plan_items[plan_id] = [(i["tuss_code"], i["agreed_price"]) for i in items]
        created_contracts += 1

    print(f"✓ Convênios/planos/contratos: {created_companies} operadora(s) nova(s), {created_plans} plano(s) novo(s), {created_contracts} contrato(s) novo(s) — {len(plan_items)} plano(s) prontos para faturar")
    return plan_items


def get_or_create_professionals(api: Api) -> list[dict]:
    existing = {p["full_name"]: p for p in api.get("/api/v1/professionals?include_inactive=true")}
    weekday_grid = [{"weekday": wd, "start_time": "08:00:00", "end_time": "12:00:00"} for wd in range(1, 6)] + [
        {"weekday": wd, "start_time": "14:00:00", "end_time": "18:00:00"} for wd in range(1, 6)
    ]
    professionals = []
    created = 0
    for full_name, registry, specialty in PROFESSIONAL_NAMES:
        prof = existing.get(full_name)
        if prof is None:
            availability = [] if registry is None else weekday_grid
            payload = {"full_name": full_name, "specialty": specialty, "availability": availability}
            if registry:
                payload["professional_registry"] = registry
            prof = api.post("/api/v1/professionals", payload)
            created += 1
        professionals.append(prof)
    print(f"✓ Profissionais: {created} novo(s), {len(professionals)} disponíveis para agenda (1 sem grade, de propósito)")
    return professionals


def get_or_create_patients(api: Api, count: int) -> list[dict]:
    page = api.get("/api/v1/patients?limit=200&offset=0")
    existing_names = {p["full_name"] for p in page["items"]}
    patients = list(page["items"])
    created = 0
    attempts = 0
    while len(patients) < count and attempts < count * 4:
        attempts += 1
        name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
        if name in existing_names:
            continue
        existing_names.add(name)
        patient = api.post("/api/v1/patients", {"full_name": name})
        patients.append(patient)
        created += 1
    print(f"✓ Pacientes: {created} novo(s), {len(patients)} disponíveis no total")
    return patients


def seed_appointment_history(
    api: Api,
    *,
    professionals: list[dict],
    patients: list[dict],
    plan_ids: list[str],
    history_days: int,
    problem_patient_ids: set,
) -> list[dict]:
    """Cria agendamentos com `scheduled_at` no PASSADO via POST normal
    (a API não valida que a data seja futura) e resolve o status logo em
    seguida via PATCH /appointments/{id} — mesma API que a recepção usa
    de verdade pra marcar falta/atendimento, nunca um INSERT direto (ver
    DECISÃO no topo do arquivo)."""
    rows: list[dict] = []
    today = date.today()
    for day_offset in range(history_days, 0, -1):
        day = today - timedelta(days=day_offset)
        if day.weekday() >= 5:
            continue
        for _ in range(random.randint(2, 5)):
            professional = random.choice(professionals)
            patient = random.choice(patients)
            plan_id = random.choice(plan_ids)
            procedure_code, _, _ = random.choice(PROCEDURES)
            hour = random.choice([8, 9, 10, 11, 14, 15, 16, 17])
            scheduled_at = datetime(day.year, day.month, day.day, hour, random.choice([0, 20, 40]), tzinfo=timezone.utc)

            appt = api.post(
                "/api/v1/appointments",
                {
                    "patient_id": patient["id"],
                    "insurance_plan_id": plan_id,
                    "professional_id": professional["id"],
                    "scheduled_at": scheduled_at.isoformat(),
                    "duration_minutes": random.choice([30, 40, 50]),
                    "procedure_code": procedure_code,
                },
            )

            is_problem = patient["id"] in problem_patient_ids
            roll = random.random()
            if is_problem:
                status = "no_show" if roll < 0.55 else "completed"
            else:
                status = "no_show" if roll < 0.08 else ("cancelled" if roll < 0.13 else "completed")

            patch_payload = {"status": status}
            if status == "completed" and random.random() > 0.1:
                patch_payload["cid_code"] = random.choice(CID_CODES)
            api.patch(f"/api/v1/appointments/{appt['id']}", patch_payload)

            rows.append({"id": appt["id"], "_plan_id": plan_id, "_procedure_code": procedure_code, "status": status})
    print(f"✓ {len(rows)} agendamento(s) histórico(s) criado(s) e resolvido(s) via API ({history_days} dias corridos, dias úteis)")
    return rows


def bill_completed_appointments(api: Api, history_rows: list[dict], plan_items: dict[str, list[tuple[str, float]]]) -> list[str]:
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

        billing = api.post("/api/v1/billing", {"appointment_id": appt["id"], "insurance_plan_id": plan_id, "charged_value": charged})
        billed += 1
        if billing["denial_risk_level"] in ("medium", "high"):
            risky_billing_ids.append(billing["id"])

        if random.random() < 0.4:
            received = charged if random.random() < 0.6 else round(charged * random.uniform(0.75, 0.95), 2)
            api.post(f"/api/v1/billing/{billing['id']}/settle", {"received_value": received})
    print(f"✓ {billed} faturamento(s) gerado(s) via API ({len(risky_billing_ids)} com risco médio/alto)")
    return risky_billing_ids


def create_future_appointments(
    api: Api,
    *,
    professionals: list[dict],
    patients: list[dict],
    plan_ids: list[str],
    future_days: int,
    problem_patient_ids: set,
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
            appt = api.post(
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
    print(f"✓ {len(appointments)} agendamento(s) futuro(s) criado(s) via API (risco de falta calculado ao vivo)")
    return appointments


def create_denial_appeals(api: Api, billing_ids: list[str], limit: int = 4) -> None:
    statuses_flow = ["aberto", "protocolado", "deferido", "indeferido"]
    created = 0
    for billing_id in billing_ids[:limit]:
        denied_at = date.today() - timedelta(days=random.randint(5, 25))
        appeal = api.post(
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
            api.post(f"/api/v1/denial-appeals/{appeal['id']}/file", {})
        if target_status in ("deferido", "indeferido"):
            api.post(
                f"/api/v1/denial-appeals/{appeal['id']}/resolve",
                {"status": target_status, "resolution_notes": "Resolução simulada para demonstração."},
            )
        created += 1
    print(f"✓ {created} recurso(s) de glosa aberto(s), em estágios variados")


def print_dashboard_summary(api: Api) -> None:
    today = date.today()
    date_from = (today - timedelta(days=30)).isoformat()
    date_to = today.isoformat()

    summary = api.get(f"/api/v1/analytics/executive-summary?date_from={date_from}&date_to={date_to}")
    agenda = api.get(f"/api/v1/analytics/agenda-metrics?date_from={date_from}&date_to={date_to}")
    insights = api.get(f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}")
    health = api.get("/api/v1/analytics/health-score")

    print("\n" + "=" * 72)
    print(f"RESUMO (janela: {date_from} a {date_to})")
    print("=" * 72)
    print(f"Total faturado:              R$ {summary['total_billed']['value']:,.2f}")
    print(f"Buraco financeiro:           R$ {summary['financial_hole']['value']:,.2f}")
    print(f"Faturamento de alto risco:   {summary['high_risk_pending_count']} pendente(s)")
    print(f"Receita em risco (no-show):  R$ {agenda['estimated_revenue_at_risk']:,.2f}")
    print(f"Nota de saúde financeira:    {health['score'] if health['score'] is not None else 'sem amostra suficiente ainda'}")
    print(f"Insights automáticos:        {len(insights['insights'])} gerado(s)")
    for insight in insights["insights"][:6]:
        impact = f" (R$ {insight['financial_impact']:,.2f})" if insight.get("financial_impact") else ""
        print(f"  [{insight['severity']}] {insight['title']}{impact}")
    print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(description="Popula um tenant EXISTENTE com dado sintético realista, 100% via API HTTP.")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--patients", type=int, default=25)
    parser.add_argument("--history-days", type=int, default=60)
    parser.add_argument("--future-days", type=int, default=10)
    args = parser.parse_args()

    api = Api(args.base_url)
    api.login(args.email, args.password)
    tenant = api.get("/api/v1/tenant")
    print(f"✓ Login OK — tenant: {tenant['trade_name']} ({tenant['id']})")

    plan_items = get_or_create_insurance_companies_and_plans(api)
    plan_ids = list(plan_items.keys())
    professionals = get_or_create_professionals(api)
    patients = get_or_create_patients(api, args.patients)
    problem_patient_ids = {p["id"] for p in random.sample(patients, k=min(5, len(patients)))}

    history_rows = seed_appointment_history(
        api,
        professionals=professionals,
        patients=patients,
        plan_ids=plan_ids,
        history_days=args.history_days,
        problem_patient_ids=problem_patient_ids,
    )
    risky_billing_ids = bill_completed_appointments(api, history_rows, plan_items)
    create_future_appointments(
        api,
        professionals=professionals,
        patients=patients,
        plan_ids=plan_ids,
        future_days=args.future_days,
        problem_patient_ids=problem_patient_ids,
    )
    create_denial_appeals(api, risky_billing_ids)
    print_dashboard_summary(api)


if __name__ == "__main__":
    main()
