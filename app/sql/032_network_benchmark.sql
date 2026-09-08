-- app/sql/032_network_benchmark.sql
--
-- Comparativo entre clínicas (Sala de Comando 2.0, Nível 1 do roadmap
-- "só existe em escala"): sua taxa de glosa e de falta contra a mediana
-- de OUTRAS clínicas ativas na base — o fosso competitivo real do
-- produto (nenhum ERP de clínica individual consegue oferecer isto).
--
-- DECISÃO — role NOVA (network_benchmark_owner), não reaproveita
-- platform_reporting_owner
-------------------------------------------------------------------------
-- Mesmo raciocínio já documentado em 026_platform_customer_success.sql:
-- platform_reporting_owner existe para o painel INTERNO da equipe da
-- Insighta (nunca visível a cliente). Esta função é chamada por um
-- usuário de CLÍNICA comum, autenticado normalmente — misturar as duas
-- na mesma role aumentaria o raio de estrago de qualquer bug futuro em
-- qualquer uma das duas. Role própria, GRANT SELECT só no que esta
-- função de fato lê (billing, appointments, tenants — nada de
-- patients/users/audit_log, que platform_reporting_owner tem e esta
-- função não precisa).
--
-- DECISÃO — a função NUNCA devolve uma linha por tenant, só agregado
-------------------------------------------------------------------------
-- Isto é a garantia de privacidade de verdade, não uma convenção de API
-- que um bug no service poderia furar: mesmo que o chamador (Python)
-- tivesse um bug amanhã, esta função SQL não tem como devolver o dado
-- de uma clínica específica para outra — o retorno é sempre "sua taxa"
-- + "mediana de outras" + "quantas entraram na mediana", nunca uma
-- lista. O piso `min_cohort` (mínimo de clínicas na mediana) é aplicado
-- AQUI DENTRO da função, não no Python chamador — devolve NULL quando
-- não há clínicas suficientes, em vez de confiar em quem chama para
-- decidir esconder o número.
--
-- DECISÃO — amostra mínima POR CLÍNICA (>= 5) antes dela entrar no
-- cálculo da mediana de rede
-------------------------------------------------------------------------
-- Uma clínica com 1 faturamento no período teria taxa 0% ou 100% —
-- ruído puro que distorceria a mediana da rede para todo mundo. Mesmo
-- princípio de MIN_SAMPLE_SIZE já usado em no_show_risk_engine.py e
-- smart_insights_engine.py, aplicado aqui à mediana entre clínicas.
DROP FUNCTION IF EXISTS core.network_glosa_no_show_benchmark(UUID, INT, INT);

CREATE FUNCTION core.network_glosa_no_show_benchmark(
    requesting_tenant_id UUID,
    window_days INT DEFAULT 90,
    min_cohort INT DEFAULT 5
)
RETURNS TABLE (
    your_denial_rate       NUMERIC,
    your_denial_sample     INT,
    network_denial_median  NUMERIC,
    denial_cohort_size     INT,
    your_no_show_rate      NUMERIC,
    your_no_show_sample    INT,
    network_no_show_median NUMERIC,
    no_show_cohort_size    INT
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = core, pg_temp
STABLE
AS $$
    WITH window_bounds AS (
        SELECT now() - (window_days || ' days')::interval AS start_ts
    ),
    tenant_denial AS (
        SELECT
            b.tenant_id,
            SUM(CASE WHEN b.denial_risk_level IN ('medium', 'high') THEN 1 ELSE 0 END)::numeric
                / NULLIF(COUNT(*), 0) AS rate,
            COUNT(*) AS sample
        FROM core.billing b, window_bounds w
        WHERE b.created_at >= w.start_ts
        GROUP BY b.tenant_id
    ),
    tenant_no_show AS (
        SELECT
            a.tenant_id,
            SUM(CASE WHEN a.status = 'no_show' THEN 1 ELSE 0 END)::numeric
                / NULLIF(COUNT(*), 0) AS rate,
            COUNT(*) AS sample
        FROM core.appointments a, window_bounds w
        WHERE a.scheduled_at >= w.start_ts AND a.status IN ('completed', 'no_show')
        GROUP BY a.tenant_id
    ),
    active_tenant_ids AS (
        SELECT id FROM core.tenants WHERE is_active
    ),
    other_denial AS (
        SELECT td.rate
        FROM tenant_denial td
        JOIN active_tenant_ids act ON act.id = td.tenant_id
        WHERE td.tenant_id != requesting_tenant_id AND td.sample >= 5
    ),
    other_no_show AS (
        SELECT tns.rate
        FROM tenant_no_show tns
        JOIN active_tenant_ids act ON act.id = tns.tenant_id
        WHERE tns.tenant_id != requesting_tenant_id AND tns.sample >= 5
    )
    SELECT
        (SELECT rate FROM tenant_denial WHERE tenant_id = requesting_tenant_id),
        COALESCE((SELECT sample FROM tenant_denial WHERE tenant_id = requesting_tenant_id), 0)::int,
        CASE WHEN (SELECT COUNT(*) FROM other_denial) >= min_cohort
             THEN (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY rate) FROM other_denial)
             ELSE NULL END,
        (SELECT COUNT(*) FROM other_denial)::int,
        (SELECT rate FROM tenant_no_show WHERE tenant_id = requesting_tenant_id),
        COALESCE((SELECT sample FROM tenant_no_show WHERE tenant_id = requesting_tenant_id), 0)::int,
        CASE WHEN (SELECT COUNT(*) FROM other_no_show) >= min_cohort
             THEN (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY rate) FROM other_no_show)
             ELSE NULL END,
        (SELECT COUNT(*) FROM other_no_show)::int;
$$;

COMMENT ON FUNCTION core.network_glosa_no_show_benchmark IS
  'Comparativo entre clínicas da Sala de Comando — devolve a taxa da '
  'clínica solicitante + a MEDIANA agregada de outras clínicas ativas '
  '(nunca uma linha por clínica), com piso de amostra mínima embutido '
  'na própria função, não confiado ao chamador.';
