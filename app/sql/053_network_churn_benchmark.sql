-- app/sql/053_network_churn_benchmark.sql
--
-- "Equilíbrio Insighta" (Balanced Scorecard, perna Cliente, mecanismo 4)
-- — mesma arquitetura EXATA de core.network_glosa_no_show_benchmark
-- (032/048): sua taxa de churn precoce contra a mediana de OUTRAS
-- clínicas ativas, com piso de amostra mínima por clínica embutido na
-- própria função e segmentação por Tenant.specialty quando o cohort
-- segmentado atinge min_cohort. Função SEPARADA (mesmo padrão de
-- core.network_revenue_growth_benchmark em 041) em vez de estender a
-- função de glosa/falta — churn precoce não tem janela de período
-- (é sempre "a partir de agora", ver DECISÃO em
-- AnalyticsService.get_early_churn_risk), então o formato de parâmetros
-- já é diferente (sem window_days).
--
-- DECISÃO — replica _EARLY_CHURN_CTE (analytics_repository.py) POR
-- TENANT, não só para o tenant solicitante
-------------------------------------------------------------------------
-- "Paciente em risco de churn precoce" já é calculado hoje só para a
-- própria clínica (RLS). Esta função reaproveita EXATAMENTE a mesma
-- régua (>= 3 visitas com histórico, intervalo médio calculável, ainda
-- dentro dos 365 dias que o transformaria em "inativo" de verdade, mas
-- já ausente por mais que 2x o próprio intervalo médio) — só que
-- agrupada por tenant_id em vez de filtrada por RLS a um tenant só.
-- Mesmos valores de min_visits (3), gap_multiplier (2.0) e
-- inactive_after_days (365) de _EARLY_CHURN_MIN_VISITS/
-- _EARLY_CHURN_GAP_MULTIPLIER/_INACTIVE_PATIENT_AFTER_DAYS em
-- analytics_service.py — hardcoded aqui dentro da função SQL pelo mesmo
-- motivo de min_cohort=5: um comparativo entre clínicas não pode
-- depender de cada clínica estar usando a mesma calibração local.
DROP FUNCTION IF EXISTS core.network_churn_benchmark(UUID, INT);

CREATE FUNCTION core.network_churn_benchmark(
    requesting_tenant_id UUID,
    min_cohort INT DEFAULT 5
)
RETURNS TABLE (
    your_churn_rate           NUMERIC,
    your_churn_sample         INT,
    network_churn_median      NUMERIC,
    churn_cohort_size         INT,
    churn_cohort_is_segmented BOOLEAN
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = core, pg_temp
STABLE
AS $$
    WITH requesting_specialty AS (
        SELECT specialty FROM core.tenants WHERE id = requesting_tenant_id
    ),
    tenant_visits AS (
        SELECT tenant_id, patient_id, scheduled_at,
               LAG(scheduled_at) OVER (PARTITION BY patient_id ORDER BY scheduled_at) AS prev_at
        FROM core.appointments
        WHERE status != 'cancelled'
    ),
    tenant_patient_stats AS (
        SELECT tenant_id, patient_id,
               MAX(scheduled_at) AS last_appointment_at,
               COUNT(*) AS visit_count,
               AVG(EXTRACT(EPOCH FROM (scheduled_at - prev_at)) / 86400.0)
                   FILTER (WHERE prev_at IS NOT NULL) AS avg_interval_days
        FROM tenant_visits
        GROUP BY tenant_id, patient_id
    ),
    qualified_patients AS (
        SELECT tenant_id, patient_id, last_appointment_at, avg_interval_days
        FROM tenant_patient_stats
        WHERE visit_count >= 3
          AND avg_interval_days IS NOT NULL
          AND avg_interval_days > 0
          AND last_appointment_at >= now() - INTERVAL '365 days'
    ),
    tenant_churn AS (
        SELECT
            tenant_id,
            SUM(CASE WHEN last_appointment_at <= now() - (avg_interval_days * 2.0) * INTERVAL '1 day'
                     THEN 1 ELSE 0 END)::numeric / NULLIF(COUNT(*), 0) AS rate,
            COUNT(*) AS sample
        FROM qualified_patients
        GROUP BY tenant_id
    ),
    active_tenants AS (
        SELECT id, specialty FROM core.tenants WHERE is_active
    ),
    other_churn_all AS (
        SELECT tc.rate
        FROM tenant_churn tc
        JOIN active_tenants act ON act.id = tc.tenant_id
        WHERE tc.tenant_id != requesting_tenant_id AND tc.sample >= 5
    ),
    other_churn_segment AS (
        SELECT tc.rate
        FROM tenant_churn tc
        JOIN active_tenants act ON act.id = tc.tenant_id
        WHERE tc.tenant_id != requesting_tenant_id AND tc.sample >= 5
          AND act.specialty IS NOT NULL
          AND act.specialty = (SELECT specialty FROM requesting_specialty)
    ),
    use_segment AS (
        SELECT
            (SELECT specialty FROM requesting_specialty) IS NOT NULL
                AND (SELECT COUNT(*) FROM other_churn_segment) >= min_cohort AS churn
    ),
    other_churn AS (
        SELECT rate FROM other_churn_segment WHERE (SELECT churn FROM use_segment)
        UNION ALL
        SELECT rate FROM other_churn_all WHERE NOT (SELECT churn FROM use_segment)
    )
    SELECT
        (SELECT rate FROM tenant_churn WHERE tenant_id = requesting_tenant_id),
        COALESCE((SELECT sample FROM tenant_churn WHERE tenant_id = requesting_tenant_id), 0)::int,
        CASE WHEN (SELECT COUNT(*) FROM other_churn) >= min_cohort
             THEN (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY rate) FROM other_churn)
             ELSE NULL END,
        (SELECT COUNT(*) FROM other_churn)::int,
        (SELECT churn FROM use_segment);
$$;

COMMENT ON FUNCTION core.network_churn_benchmark IS
  'Comparativo de churn precoce entre clínicas — mesma arquitetura de '
  'core.network_glosa_no_show_benchmark, aplicada à taxa de pacientes '
  'em risco de churn precoce em vez de glosa/falta. Piso de amostra '
  'mínima embutido na própria função; segmenta por Tenant.specialty '
  'quando o cohort segmentado atinge min_cohort.';
