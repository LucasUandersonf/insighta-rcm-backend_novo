-- app/sql/026_platform_customer_success.sql
--
-- Customer Success orientado a dados: uma visão AGREGADA de uso por
-- clínica, para a EQUIPE QUE OPERA A PLATAFORMA (Insighta) enxergar quem
-- está engajado e quem está em risco de cancelar antes que isso vire uma
-- perda — nunca para um usuário de clínica ver o próprio "placar", nem,
-- pior, o de outra clínica.
--
-- DECISÃO — role NOVA (platform_reporting_owner), não reaproveita
-- auth_resolver_owner
-------------------------------------------------------------------------
-- auth_resolver_owner existe para um problema bem específico: resolver
-- QUEM É O TENANT antes de existir contexto de tenant (login, reset de
-- senha, chave de API) — sempre devolvendo candidatas ESTREITAS (um
-- usuário, uma chave), nunca um agregado cross-tenant. Esta função é
-- outra categoria de problema: uma vez que já sabemos quem está pedindo
-- (o operador da própria plataforma, autenticado por senha própria — ver
-- app/api/platform_admin_auth.py, nada a ver com login de clínica), ela
-- devolve dado agregado de TODOS os tenants de uma vez, de propósito.
-- Misturar as duas responsabilidades na mesma role aumentaria o raio de
-- estrago de qualquer bug/vazamento futuro em qualquer uma das duas —
-- por isso esta função ganha uma role própria, com GRANT SELECT só nas
-- tabelas que este relatório de fato lê.
--
-- DECISÃO — nunca expõe dado clínico/PII, só contagens e datas
-------------------------------------------------------------------------
-- "Uso do produto" aqui é medido por CONTAGEM de atividade (quantos
-- eventos de auditoria, quantos pacientes cadastrados, quando foi a
-- última ação) — nunca por conteúdo (nome de paciente, CID, valor
-- financeiro individual). O objetivo é "esse cliente está usando a
-- ferramenta?", não "o que esse cliente está fazendo com ela?".
--
-- DECISÃO — reaproveita core.audit_log como sinal de "última atividade"
-------------------------------------------------------------------------
-- Em vez de instrumentar um evento novo só para isto, MAX(created_at) de
-- core.audit_log (já escrito de verdade em toda mutação sensível desde a
-- rodada de LGPD/auditoria) já é um proxy real e imediato de "quando essa
-- clínica foi vista pela última vez fazendo alguma coisa" — reuso de
-- infraestrutura já existente, sem duplicar instrumentação.
DROP FUNCTION IF EXISTS core.platform_tenant_usage_summary();

CREATE FUNCTION core.platform_tenant_usage_summary()
RETURNS TABLE (
    tenant_id            UUID,
    trade_name           VARCHAR,
    plan_tier            VARCHAR,
    tenant_is_active     BOOLEAN,
    tenant_created_at    TIMESTAMPTZ,
    active_users         BIGINT,
    last_activity_at     TIMESTAMPTZ,
    events_last_30d      BIGINT,
    patients_total       BIGINT,
    appointments_last_30d BIGINT,
    billings_last_30d    BIGINT
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = core, pg_temp
STABLE
AS $$
    SELECT
        t.id,
        t.trade_name,
        t.plan_tier,
        t.is_active,
        t.created_at,
        (SELECT COUNT(*) FROM core.users u WHERE u.tenant_id = t.id AND u.is_active) AS active_users,
        (SELECT MAX(a.created_at) FROM core.audit_log a WHERE a.tenant_id = t.id) AS last_activity_at,
        (SELECT COUNT(*) FROM core.audit_log a WHERE a.tenant_id = t.id AND a.created_at >= now() - interval '30 days') AS events_last_30d,
        (SELECT COUNT(*) FROM core.patients p WHERE p.tenant_id = t.id) AS patients_total,
        (SELECT COUNT(*) FROM core.appointments ap WHERE ap.tenant_id = t.id AND ap.created_at >= now() - interval '30 days') AS appointments_last_30d,
        (SELECT COUNT(*) FROM core.billing b WHERE b.tenant_id = t.id AND b.created_at >= now() - interval '30 days') AS billings_last_30d
    FROM core.tenants t
    ORDER BY t.trade_name;
$$;

COMMENT ON FUNCTION core.platform_tenant_usage_summary IS
  'Único ponto do sistema autorizado a agregar dado cross-tenant para o '
  'painel interno de Customer Success da própria Insighta. Nunca exposto '
  'a nenhum usuário de clínica — só ao operador da plataforma autenticado '
  'por senha própria (ver app/api/platform_admin_auth.py).';
