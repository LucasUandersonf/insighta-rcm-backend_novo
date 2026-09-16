-- app/sql/041_network_revenue_growth_benchmark.sql
--
-- Épico F3.3 do Plano Diretor ("Metas e cenários orientados a dados") —
-- "meta anual sugerida (crescimento histórico + percentil de rede)".
-- Mesmo espírito e MESMA role (network_benchmark_owner, ver DECISÃO
-- completa em 032_network_benchmark.sql) do Comparativo de Clínicas:
-- SECURITY DEFINER, cross-tenant, nunca devolve uma linha por clínica —
-- só "sua taxa" + "mediana agregada de outras", com piso de amostra
-- embutido NA PRÓPRIA função.
--
-- DECISÃO — duas sugestões INDEPENDENTES, nunca uma "média" que finge
-- ser uma coisa só
-------------------------------------------------------------------------
-- "Crescimento histórico" (seu próprio) e "percentil de rede" (ritmo de
-- outras clínicas) são duas fontes de informação DIFERENTES — misturar
-- num único número escondido seria inventar uma confiança que nenhuma
-- das duas partes sozinha sustenta. A função devolve as duas taxas de
-- crescimento (a sua e a mediana da rede); o Python (NetworkBenchmarkService)
-- projeta cada uma sobre o MESMO faturamento base (seus últimos 12
-- meses), gerando duas sugestões que o usuário compara e escolhe — nunca
-- uma aplicada automaticamente.
--
-- your_growth_rate é NULL quando os 12 meses ANTERIORES aos últimos 12
-- não têm faturamento (base zero, taxa indefinida — mesmo princípio de
-- "None sobre zero" usado em toda parte do produto, ver _delta_pct no
-- service). network_growth_median é NULL abaixo do cohort mínimo (só
-- entram na mediana clínicas com taxa própria definida, mesma regra
-- de network_glosa_no_show_benchmark).
DROP FUNCTION IF EXISTS core.network_revenue_growth_benchmark(UUID, INT);

CREATE FUNCTION core.network_revenue_growth_benchmark(
    requesting_tenant_id UUID,
    min_cohort INT DEFAULT 5
)
RETURNS TABLE (
    your_trailing_12mo_total NUMERIC,
    your_prior_12mo_total    NUMERIC,
    your_growth_rate         NUMERIC,
    network_growth_median    NUMERIC,
    growth_cohort_size       INT
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = core, pg_temp
STABLE
AS $$
    WITH bounds AS (
        SELECT now() AS as_of
    ),
    tenant_totals AS (
        SELECT
            b.tenant_id,
            SUM(CASE WHEN b.created_at >= (SELECT as_of FROM bounds) - INTERVAL '12 months'
                     THEN b.charged_value ELSE 0 END) AS trailing_12mo,
            SUM(CASE WHEN b.created_at >= (SELECT as_of FROM bounds) - INTERVAL '24 months'
                      AND b.created_at < (SELECT as_of FROM bounds) - INTERVAL '12 months'
                     THEN b.charged_value ELSE 0 END) AS prior_12mo
        FROM core.billing b
        GROUP BY b.tenant_id
    ),
    tenant_growth AS (
        SELECT
            tenant_id,
            trailing_12mo,
            prior_12mo,
            CASE WHEN prior_12mo > 0 THEN (trailing_12mo - prior_12mo) / prior_12mo ELSE NULL END AS growth_rate
        FROM tenant_totals
    ),
    active_tenant_ids AS (
        SELECT id FROM core.tenants WHERE is_active
    ),
    other_growth AS (
        SELECT tg.growth_rate
        FROM tenant_growth tg
        JOIN active_tenant_ids act ON act.id = tg.tenant_id
        WHERE tg.tenant_id != requesting_tenant_id AND tg.growth_rate IS NOT NULL
    )
    SELECT
        COALESCE((SELECT trailing_12mo FROM tenant_growth WHERE tenant_id = requesting_tenant_id), 0),
        COALESCE((SELECT prior_12mo FROM tenant_growth WHERE tenant_id = requesting_tenant_id), 0),
        (SELECT growth_rate FROM tenant_growth WHERE tenant_id = requesting_tenant_id),
        CASE WHEN (SELECT COUNT(*) FROM other_growth) >= min_cohort
             THEN (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY growth_rate) FROM other_growth)
             ELSE NULL END,
        (SELECT COUNT(*) FROM other_growth)::int;
$$;

COMMENT ON FUNCTION core.network_revenue_growth_benchmark IS
  'Meta anual sugerida (Épico F3.3) — devolve o faturamento dos últimos '
  '12 meses da clínica solicitante, os 12 meses anteriores, sua taxa de '
  'crescimento própria, e a MEDIANA de crescimento de outras clínicas '
  'ativas (nunca uma linha por clínica), com piso de amostra mínima '
  'embutido na própria função.';
