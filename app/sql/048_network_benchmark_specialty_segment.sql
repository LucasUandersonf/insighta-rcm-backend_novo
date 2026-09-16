-- app/sql/048_network_benchmark_specialty_segment.sql
--
-- "Mapa de Dados Insighta" — pilar "Comparativo & rede" (7,5 → 9,0):
-- Tenant.specialty existe desde o épico F2.1 (040_tenant_calibration_fields.sql)
-- mas até aqui era só metadado DESCRITIVO — nunca usado pra selecionar
-- o cohort do Comparativo entre Clínicas. Hoje uma clínica de estética
-- pode estar sendo comparada contra uma mediana que inclui perfil bem
-- diferente (ex.: saúde mental, com taxa de falta estruturalmente
-- maior) — a mesma limitação que threshold_calibration.py já documenta
-- para limiares internos, agora resolvida no lado do Comparativo.
--
-- DECISÃO — segmenta só quando o cohort segmentado ATINGE min_cohort,
-- senão cai pro cohort geral (nunca erro, nunca None por escassez
-- evitável)
-------------------------------------------------------------------------
-- Mesmo princípio de "nunca inventar confiança sem amostra" do resto do
-- produto: uma clínica de especialidade rara comparada só contra 1-2
-- pares da mesma especialidade teria uma mediana tão ruidosa quanto a
-- taxa dela sozinha. Prefere o cohort GERAL (mais amplo, mais estável)
-- a um cohort segmentado pequeno demais pra confiar. `*_cohort_is_segmented`
-- diz ao frontend qual dos dois foi de fato usado — nunca esconde a
-- diferença silenciosamente.
--
-- DROP + CREATE (não CREATE OR REPLACE): o RETURNS TABLE ganhou 2
-- colunas novas (*_cohort_is_segmented) — Postgres recusa REPLACE
-- quando o tipo de retorno muda. Mesmo padrão do arquivo original
-- (032_network_benchmark.sql) por esse mesmo motivo. A identidade da
-- função pra ALTER OWNER/GRANT em bootstrap_db.py é por NOME + TIPOS
-- DE ENTRADA (UUID, INT, INT), que não mudaram — _ensure_roles() já
-- roda DEPOIS deste arquivo em todo bootstrap (ver ORDEM no próprio
-- bootstrap_db.py) e reaplica ownership/grant sozinho, sem precisar
-- repetir aqui.
DROP FUNCTION IF EXISTS core.network_glosa_no_show_benchmark(UUID, INT, INT);

CREATE FUNCTION core.network_glosa_no_show_benchmark(
    requesting_tenant_id UUID,
    window_days INT DEFAULT 90,
    min_cohort INT DEFAULT 5
)
RETURNS TABLE (
    your_denial_rate            NUMERIC,
    your_denial_sample          INT,
    network_denial_median       NUMERIC,
    denial_cohort_size          INT,
    denial_cohort_is_segmented  BOOLEAN,
    your_no_show_rate           NUMERIC,
    your_no_show_sample         INT,
    network_no_show_median      NUMERIC,
    no_show_cohort_size         INT,
    no_show_cohort_is_segmented BOOLEAN
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = core, pg_temp
STABLE
AS $$
    WITH window_bounds AS (
        SELECT now() - (window_days || ' days')::interval AS start_ts
    ),
    requesting_specialty AS (
        SELECT specialty FROM core.tenants WHERE id = requesting_tenant_id
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
    active_tenants AS (
        SELECT id, specialty FROM core.tenants WHERE is_active
    ),
    other_denial_all AS (
        SELECT td.rate
        FROM tenant_denial td
        JOIN active_tenants act ON act.id = td.tenant_id
        WHERE td.tenant_id != requesting_tenant_id AND td.sample >= 5
    ),
    other_denial_segment AS (
        SELECT td.rate
        FROM tenant_denial td
        JOIN active_tenants act ON act.id = td.tenant_id
        WHERE td.tenant_id != requesting_tenant_id AND td.sample >= 5
          AND act.specialty IS NOT NULL
          AND act.specialty = (SELECT specialty FROM requesting_specialty)
    ),
    other_no_show_all AS (
        SELECT tns.rate
        FROM tenant_no_show tns
        JOIN active_tenants act ON act.id = tns.tenant_id
        WHERE tns.tenant_id != requesting_tenant_id AND tns.sample >= 5
    ),
    other_no_show_segment AS (
        SELECT tns.rate
        FROM tenant_no_show tns
        JOIN active_tenants act ON act.id = tns.tenant_id
        WHERE tns.tenant_id != requesting_tenant_id AND tns.sample >= 5
          AND act.specialty IS NOT NULL
          AND act.specialty = (SELECT specialty FROM requesting_specialty)
    ),
    use_segment AS (
        SELECT
            (SELECT specialty FROM requesting_specialty) IS NOT NULL
                AND (SELECT COUNT(*) FROM other_denial_segment) >= min_cohort AS denial,
            (SELECT specialty FROM requesting_specialty) IS NOT NULL
                AND (SELECT COUNT(*) FROM other_no_show_segment) >= min_cohort AS no_show
    ),
    other_denial AS (
        SELECT rate FROM other_denial_segment WHERE (SELECT denial FROM use_segment)
        UNION ALL
        SELECT rate FROM other_denial_all WHERE NOT (SELECT denial FROM use_segment)
    ),
    other_no_show AS (
        SELECT rate FROM other_no_show_segment WHERE (SELECT no_show FROM use_segment)
        UNION ALL
        SELECT rate FROM other_no_show_all WHERE NOT (SELECT no_show FROM use_segment)
    )
    SELECT
        (SELECT rate FROM tenant_denial WHERE tenant_id = requesting_tenant_id),
        COALESCE((SELECT sample FROM tenant_denial WHERE tenant_id = requesting_tenant_id), 0)::int,
        CASE WHEN (SELECT COUNT(*) FROM other_denial) >= min_cohort
             THEN (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY rate) FROM other_denial)
             ELSE NULL END,
        (SELECT COUNT(*) FROM other_denial)::int,
        (SELECT denial FROM use_segment),
        (SELECT rate FROM tenant_no_show WHERE tenant_id = requesting_tenant_id),
        COALESCE((SELECT sample FROM tenant_no_show WHERE tenant_id = requesting_tenant_id), 0)::int,
        CASE WHEN (SELECT COUNT(*) FROM other_no_show) >= min_cohort
             THEN (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY rate) FROM other_no_show)
             ELSE NULL END,
        (SELECT COUNT(*) FROM other_no_show)::int,
        (SELECT no_show FROM use_segment);
$$;

COMMENT ON FUNCTION core.network_glosa_no_show_benchmark IS
  'Comparativo entre clínicas da Sala de Comando — devolve a taxa da '
  'clínica solicitante + a MEDIANA agregada de outras clínicas ativas '
  '(nunca uma linha por clínica), com piso de amostra mínima embutido '
  'na própria função. Segmenta por Tenant.specialty quando o cohort '
  'segmentado atinge min_cohort (ver *_cohort_is_segmented); senão usa '
  'o cohort geral.';
