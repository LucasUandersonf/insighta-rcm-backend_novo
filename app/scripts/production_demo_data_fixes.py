"""
app/scripts/production_demo_data_fixes.py

Script de correção PONTUAL (não é um worker agendado nem faz parte do
pipeline normal) — fecha as lacunas de CADASTRO/CONFIGURAÇÃO apontadas
pelo "Raio-X da Sala de Comando" nos dados de demonstração, ANTES de
qualquer cliente real entrar no sistema (pedido explícito do usuário:
"o objetivo é estes dados de teste darem positivo antes de colocar
qualquer paciente no sistema").

ESCOPO — só "Clínica Exemplo", NUNCA o cadastro vazio
-------------------------------------------------------------------------
Filtra por trade_name = 'Clínica Exemplo' em toda escrita. O segundo
tenant (cadastro vazio, nome-fantasia ainda como placeholder) nunca é
tocado — não há dado nenhum lá para "completar" e mexer nele seria
fabricar uma clínica que não existe.

DECISÃO — cada valor gravado é DERIVADO de dado real já existente,
nunca inventado do nada
-------------------------------------------------------------------------
1. Grade do profissional sem disponibilidade cadastrada: copia o padrão
   de dia/horário MAIS COMUM entre os outros profissionais do mesmo
   tenant (moda, não um horário genérico "9 às 18" inventado).
2. CID das consultas sem esse dado: sorteia entre os CID já usados de
   verdade nas OUTRAS consultas do mesmo tenant (nunca um código novo
   que não tem nenhuma consulta real por trás).
3. Meta anual de faturamento: annualiza o volume REAL de cobrança já
   registrado (soma de charged_value dividida pelo número de dias
   observados, vezes 365) — nunca um número redondo arbitrário.
4. Guia vinculada a cada billing sem guia_id: cria 1 Guia por billing
   (tipo "consulta", o mais comum no padrão TISS/ANS), já que o dado de
   origem (CSV importado sem as colunas guia_tipo/guia_numero) não
   carrega informação suficiente para diferenciar SADT de consulta
   simples — documentado aqui como simplificação, não fingido como
   dado real de guia.

DECISÃO — DATABASE_ADMIN_URL (bypassa RLS), mesma razão do script de
diagnóstico (data_quality_report.py): já sabemos o tenant_id antes de
escrever (resolvido por trade_name), então a única razão para usar o
superusuário aqui é a mesma de sempre — nenhuma regra de negócio nova,
só não depender de uma sessão app_runtime com tenant já setado.

DECISÃO — nunca imprime linha crua de paciente
-------------------------------------------------------------------------
Só contagens/valores agregados vão pro log — mesmo padrão do script de
diagnóstico.
"""
import asyncio
import logging
import os
import random
import uuid
from collections import Counter
from datetime import date, timedelta

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("production_demo_data_fixes")

_TENANT_TRADE_NAME = "Clínica Exemplo"
_DEFAULT_GUIA_TIPO = "consulta"


async def _resolve_tenant_id(conn: asyncpg.Connection) -> uuid.UUID | None:
    row = await conn.fetchrow("SELECT id FROM core.tenants WHERE trade_name = $1", _TENANT_TRADE_NAME)
    return row["id"] if row else None


async def _fix_professional_availability(conn: asyncpg.Connection, tenant_id: uuid.UUID) -> None:
    without_grid = await conn.fetch(
        """
        SELECT p.id, p.full_name
        FROM core.professionals p
        WHERE p.tenant_id = $1
          AND NOT EXISTS (SELECT 1 FROM core.professional_availability a WHERE a.professional_id = p.id)
        """,
        tenant_id,
    )
    if not without_grid:
        logger.info("Grade de profissionais: nenhum profissional sem grade — nada a fazer.")
        return

    existing_slots = await conn.fetch(
        """
        SELECT a.weekday, a.start_time, a.end_time
        FROM core.professional_availability a
        JOIN core.professionals p ON p.id = a.professional_id
        WHERE p.tenant_id = $1
        """,
        tenant_id,
    )
    if not existing_slots:
        logger.info("Grade de profissionais: nenhum OUTRO profissional tem grade cadastrada — sem padrão pra copiar, nada a fazer.")
        return

    # Moda dos blocos (weekday, start_time, end_time) entre quem já tem
    # grade — só os blocos que EMPATAM no maior número de ocorrências
    # (a moda de verdade), nunca a lista inteira ordenada por
    # frequência (isso incluiria blocos avulsos de 1 profissional
    # discordante, como um horário excepcional de um único dia).
    counter = Counter((r["weekday"], r["start_time"], r["end_time"]) for r in existing_slots)
    max_count = max(counter.values())
    most_common_pattern = [slot for slot, count in counter.items() if count == max_count]
    # Se o padrão mais comum não cobre uma grade semanal razoável (ex.:
    # só 1 bloco), usa a grade completa do profissional com MAIS blocos
    # cadastrados — ainda assim, 100% copiado de um profissional real.
    by_professional: dict[uuid.UUID, list] = {}
    detailed = await conn.fetch(
        """
        SELECT a.professional_id, a.weekday, a.start_time, a.end_time
        FROM core.professional_availability a
        JOIN core.professionals p ON p.id = a.professional_id
        WHERE p.tenant_id = $1
        """,
        tenant_id,
    )
    for r in detailed:
        by_professional.setdefault(r["professional_id"], []).append((r["weekday"], r["start_time"], r["end_time"]))
    richest_grid = max(by_professional.values(), key=len)
    grid_to_copy = most_common_pattern if len(most_common_pattern) >= 3 else richest_grid

    for professional in without_grid:
        for weekday, start_time, end_time in grid_to_copy:
            await conn.execute(
                """
                INSERT INTO core.professional_availability (id, tenant_id, professional_id, weekday, start_time, end_time)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                uuid.uuid4(), tenant_id, professional["id"], weekday, start_time, end_time,
            )
        logger.info(
            "Grade cadastrada: profissional=%s — %d bloco(s), copiado do padrão mais comum da clínica.",
            professional["full_name"], len(grid_to_copy),
        )


async def _fix_missing_cid(conn: asyncpg.Connection, tenant_id: uuid.UUID) -> None:
    known_cids = await conn.fetch(
        "SELECT cid_code FROM core.appointments WHERE tenant_id = $1 AND cid_code IS NOT NULL", tenant_id
    )
    if not known_cids:
        logger.info("CID: nenhum CID real cadastrado neste tenant ainda — sem vocabulário pra copiar, nada a fazer.")
        return
    pool = [r["cid_code"] for r in known_cids]

    missing = await conn.fetch(
        "SELECT id FROM core.appointments WHERE tenant_id = $1 AND cid_code IS NULL", tenant_id
    )
    if not missing:
        logger.info("CID: nenhuma consulta sem CID — nada a fazer.")
        return

    rng = random.Random(42)  # determinístico — reexecutar o script não muda o resultado
    for row in missing:
        chosen = rng.choice(pool)
        await conn.execute("UPDATE core.appointments SET cid_code = $1 WHERE id = $2", chosen, row["id"])
    logger.info("CID preenchido em %d consulta(s), sorteado entre os %d código(s) já usados de verdade neste tenant.", len(missing), len(set(pool)))


async def _fix_annual_revenue_goal(conn: asyncpg.Connection, tenant_id: uuid.UUID) -> None:
    current = await conn.fetchval("SELECT annual_revenue_goal FROM core.tenants WHERE id = $1", tenant_id)
    if current is not None:
        logger.info("Meta anual: já configurada (%s) — nada a fazer.", current)
        return

    row = await conn.fetchrow(
        """
        SELECT SUM(b.charged_value) AS total, MIN(a.scheduled_at) AS first_dt, MAX(a.scheduled_at) AS last_dt
        FROM core.billing b
        JOIN core.appointments a ON a.id = b.appointment_id
        WHERE b.tenant_id = $1
        """,
        tenant_id,
    )
    if row is None or row["total"] is None or row["first_dt"] is None:
        logger.info("Meta anual: sem faturamento suficiente para estimar — nada a fazer.")
        return

    observed_days = max((row["last_dt"] - row["first_dt"]).days, 1)
    daily_run_rate = float(row["total"]) / observed_days
    annual_goal = round(daily_run_rate * 365, 2)

    await conn.execute("UPDATE core.tenants SET annual_revenue_goal = $1 WHERE id = $2", annual_goal, tenant_id)
    logger.info(
        "Meta anual configurada: R$ %.2f (annualizado de R$ %.2f cobrado em %d dia(s) observados).",
        annual_goal, float(row["total"]), observed_days,
    )


async def _fix_missing_guias(conn: asyncpg.Connection, tenant_id: uuid.UUID) -> None:
    unlinked = await conn.fetch(
        "SELECT id, insurance_plan_id FROM core.billing WHERE tenant_id = $1 AND guia_id IS NULL", tenant_id
    )
    if not unlinked:
        logger.info("Guias: todo billing já está vinculado a uma guia — nada a fazer.")
        return

    counter = 1
    for row in unlinked:
        guia_id = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO core.guias (id, tenant_id, insurance_plan_id, tipo, numero)
            VALUES ($1, $2, $3, $4, $5)
            """,
            guia_id, tenant_id, row["insurance_plan_id"], _DEFAULT_GUIA_TIPO, f"DEMO-{counter:04d}",
        )
        await conn.execute("UPDATE core.billing SET guia_id = $1 WHERE id = $2", guia_id, row["id"])
        counter += 1
    logger.info(
        "Guias criadas e vinculadas: %d (tipo '%s' — simplificação documentada: o CSV de origem não trazia "
        "guia_tipo/guia_numero, então não há como diferenciar SADT de consulta simples por billing).",
        len(unlinked), _DEFAULT_GUIA_TIPO,
    )


async def run() -> None:
    admin_url = os.environ["DATABASE_ADMIN_URL"]
    conn = await asyncpg.connect(admin_url)
    try:
        tenant_id = await _resolve_tenant_id(conn)
        if tenant_id is None:
            logger.info("Tenant '%s' não encontrado — nada a fazer (proteção: nunca escreve fora deste tenant).", _TENANT_TRADE_NAME)
            return
        logger.info("Tenant resolvido: %s (id=%s)", _TENANT_TRADE_NAME, tenant_id)

        async with conn.transaction():
            await _fix_professional_availability(conn, tenant_id)
            await _fix_missing_cid(conn, tenant_id)
            await _fix_annual_revenue_goal(conn, tenant_id)
            await _fix_missing_guias(conn, tenant_id)

        logger.info("Concluído — transação commitada.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
