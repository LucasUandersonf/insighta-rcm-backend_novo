"""
app/scripts/data_quality_report.py

Script de diagnóstico SÓ-LEITURA, de uso PONTUAL (não é um worker
agendado nem faz parte do pipeline normal) — roda um conjunto de
consultas agregadas contra o banco de produção pra responder "qual é a
qualidade real do dado que alimenta a Sala de Comando hoje", pedido
explícito do usuário atuando como revisor de BI/Dados desta sessão.

DECISÃO — usa DATABASE_ADMIN_URL direto, nunca a role app_runtime
-------------------------------------------------------------------------
app_runtime é sujeita a RLS (cada conexão só enxerga o tenant setado via
SET LOCAL app.tenant_id — ver bootstrap_db.py) — correto pra servir a
aplicação, errado pra este script, que precisa agregar ACROSS tenants
(quantos tenants existem, completude de dado por tenant, etc.). O
superusuário por trás de DATABASE_ADMIN_URL não tem RLS aplicado
(mesmo motivo que o torna apto a criar roles/schema no bootstrap),
então é a credencial certa aqui — só leitura, nunca usada pra escrever.

DECISÃO — nunca imprime a DSN nem qualquer linha crua de paciente
-------------------------------------------------------------------------
Só resultados agregados (COUNT/AVG/percentuais) vão pro log. Nenhuma
consulta aqui faz SELECT de linha de paciente/nome/CPF — tudo é
contagem ou percentual.
"""
import asyncio
import logging
import os

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("data_quality_report")

_QUERIES: list[tuple[str, str]] = [
    ("Tenants — total", "SELECT count(*) AS total FROM core.tenants"),
    (
        "Tenants — lista (nome, ativo, criado em)",
        "SELECT trade_name, is_active, created_at::date FROM core.tenants ORDER BY created_at",
    ),
    (
        "Volume por tenant",
        """
        SELECT
          t.trade_name,
          (SELECT count(*) FROM core.patients p WHERE p.tenant_id = t.id) AS pacientes,
          (SELECT count(*) FROM core.appointments a WHERE a.tenant_id = t.id) AS consultas,
          (SELECT count(*) FROM core.billing b WHERE b.tenant_id = t.id) AS faturas,
          (SELECT count(*) FROM core.professionals pr WHERE pr.tenant_id = t.id) AS profissionais,
          (SELECT count(*) FROM core.contracts c WHERE c.tenant_id = t.id) AS contratos,
          (SELECT count(*) FROM core.contracts c WHERE c.tenant_id = t.id AND c.status = 'homologado') AS contratos_homologados,
          (SELECT count(*) FROM core.contract_items ci WHERE ci.tenant_id = t.id) AS itens_de_contrato,
          (SELECT count(*) FROM core.guias g WHERE g.tenant_id = t.id) AS guias,
          (SELECT count(*) FROM core.denial_appeals da WHERE da.tenant_id = t.id) AS recursos_de_glosa,
          (SELECT count(*) FROM core.insurance_plans ip WHERE ip.tenant_id = t.id) AS convenios
        FROM core.tenants t
        ORDER BY t.trade_name
        """,
    ),
    (
        "Completude de Appointments — geral",
        """
        SELECT
          count(*) AS total,
          round(100.0 * count(*) FILTER (WHERE duration_minutes IS NOT NULL) / NULLIF(count(*), 0), 1) AS pct_com_duracao,
          round(100.0 * count(*) FILTER (WHERE cid_code IS NOT NULL) / NULLIF(count(*), 0), 1) AS pct_com_cid,
          round(100.0 * count(*) FILTER (WHERE procedure_code IS NOT NULL) / NULLIF(count(*), 0), 1) AS pct_com_procedimento,
          round(100.0 * count(*) FILTER (WHERE professional_id IS NOT NULL) / NULLIF(count(*), 0), 1) AS pct_com_profissional,
          round(100.0 * count(*) FILTER (WHERE insurance_plan_id IS NOT NULL) / NULLIF(count(*), 0), 1) AS pct_com_convenio
        FROM core.appointments
        """,
    ),
    (
        "Completude de Appointments — por tenant",
        """
        SELECT
          t.trade_name,
          count(*) AS total,
          round(100.0 * count(*) FILTER (WHERE a.duration_minutes IS NOT NULL) / NULLIF(count(*), 0), 1) AS pct_com_duracao,
          round(100.0 * count(*) FILTER (WHERE a.cid_code IS NOT NULL) / NULLIF(count(*), 0), 1) AS pct_com_cid,
          round(100.0 * count(*) FILTER (WHERE a.procedure_code IS NOT NULL) / NULLIF(count(*), 0), 1) AS pct_com_procedimento,
          round(100.0 * count(*) FILTER (WHERE a.professional_id IS NOT NULL) / NULLIF(count(*), 0), 1) AS pct_com_profissional
        FROM core.appointments a
        JOIN core.tenants t ON t.id = a.tenant_id
        GROUP BY t.id, t.trade_name
        ORDER BY t.trade_name
        """,
    ),
    (
        "Completude/estado de Billing",
        """
        SELECT
          count(*) AS total,
          round(100.0 * count(*) FILTER (WHERE guia_id IS NOT NULL) / NULLIF(count(*), 0), 1) AS pct_com_guia,
          round(100.0 * count(*) FILTER (WHERE received_value IS NOT NULL) / NULLIF(count(*), 0), 1) AS pct_conciliado
        FROM core.billing
        """,
    ),
    ("Billing por status", "SELECT status, count(*) FROM core.billing GROUP BY status ORDER BY count(*) DESC"),
    (
        "Billing por nível de risco de glosa",
        "SELECT denial_risk_level, count(*) FROM core.billing GROUP BY denial_risk_level ORDER BY count(*) DESC",
    ),
    (
        "Grade de disponibilidade de profissionais",
        """
        SELECT
          (SELECT count(*) FROM core.professionals WHERE is_active) AS profissionais_ativos,
          (SELECT count(DISTINCT professional_id) FROM core.professional_availability) AS com_grade_cadastrada,
          (SELECT count(*) FROM core.professional_availability) AS total_blocos_de_grade
        """,
    ),
    (
        "Recursos de glosa por status",
        "SELECT status, count(*) FROM core.denial_appeals GROUP BY status ORDER BY count(*) DESC",
    ),
    (
        "Meta anual configurada",
        "SELECT count(*) FILTER (WHERE annual_revenue_goal IS NOT NULL) AS com_meta, count(*) AS total FROM core.tenants",
    ),
    (
        "Fotografias da Nota de Saúde (health_score_snapshots)",
        "SELECT count(*) AS total FROM core.health_score_snapshots",
    ),
    (
        "Últimas fotografias da Nota de Saúde",
        """
        SELECT t.trade_name, s.snapshot_month, s.score
        FROM core.health_score_snapshots s
        JOIN core.tenants t ON t.id = s.tenant_id
        ORDER BY s.created_at DESC
        LIMIT 10
        """,
    ),
]


async def run() -> None:
    admin_url = os.environ["DATABASE_ADMIN_URL"]
    conn = await asyncpg.connect(admin_url)
    try:
        for title, query in _QUERIES:
            logger.info("=" * 70)
            logger.info(title)
            logger.info("=" * 70)
            try:
                rows = await conn.fetch(query)
            except Exception as exc:  # nunca deixa uma tabela ausente derrubar o resto do relatório
                logger.info("(erro ao rodar — %s)", exc)
                continue
            if not rows:
                logger.info("(sem linhas)")
                continue
            for row in rows:
                logger.info(dict(row))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
