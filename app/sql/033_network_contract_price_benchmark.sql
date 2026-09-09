-- app/sql/033_network_contract_price_benchmark.sql
--
-- Oportunidades (Sala de Comando 2.0, Nível 1 do roadmap "só existe em
-- escala"): para cada procedimento que a clínica já tem homologado com
-- um convênio, mostra o preço acordado dela contra a MEDIANA de preço
-- acordado de OUTRAS clínicas para o MESMO convênio (via
-- InsurancePlan.normalized_key) e o MESMO procedimento (tuss_code) —
-- "por quanto vale a pena renegociar este item". Mesmo fosso
-- competitivo do Comparativo (032_network_benchmark.sql): nenhum ERP de
-- clínica individual consegue oferecer isto, porque exige dado de
-- VÁRIAS clínicas ao mesmo tempo.
--
-- DECISÃO — role NOVA (contract_price_benchmark_owner), não reaproveita
-- network_benchmark_owner nem platform_reporting_owner
-------------------------------------------------------------------------
-- Mesmo raciocínio já documentado em 032_network_benchmark.sql: cada
-- função cross-tenant recebe a SUA PRÓPRIA role, com GRANT SELECT só no
-- que ela de fato lê. Esta função lê contracts/contract_items/
-- insurance_plans (preço de tabela, dado de contrato) — categoria
-- diferente de billing/appointments agregado (taxas), que já é o que
-- network_benchmark_owner enxerga. Minimiza o raio de estrago de
-- qualquer bug futuro em qualquer uma das três.
--
-- DECISÃO — a função NUNCA devolve o preço de OUTRA clínica, só a
-- mediana agregada
-------------------------------------------------------------------------
-- Mesma garantia de privacidade "de verdade" do Comparativo: a query
-- devolve, por (convênio, procedimento) que a clínica solicitante TEM
-- contratado, o preço DELA + a mediana de outras + quantas clínicas
-- entraram na mediana — nunca uma lista de preços de terceiros. O piso
-- `min_cohort` é aplicado AQUI DENTRO, por grupo (convênio+procedimento),
-- não confiado ao Python chamador: um grupo sem clínicas suficientes na
-- mediana simplesmente não aparece no resultado, em vez de aparecer com
-- uma mediana calculada sobre 1-2 outras clínicas.
--
-- DECISÃO — "contrato vigente" é o MESMO critério de
-- ContractItemRepository.find_agreed_price, replicado aqui em SQL puro
-------------------------------------------------------------------------
-- status = 'homologado' AND valid_from <= hoje AND (valid_until IS NULL
-- OR valid_until >= hoje) — nunca considera rascunho/em_revisao (preço
-- ainda não confirmado por humano) nem contrato vencido/futuro.
--
-- DECISÃO — "Volume/mês" vem de Billing/Appointment da PRÓPRIA clínica,
-- não é cross-tenant
-------------------------------------------------------------------------
-- Só precisa saber "quanto isso vale POR MÊS para mim", não comparar
-- volume com outras clínicas — por isso filtra sempre por
-- b.tenant_id = requesting_tenant_id, mesmo dentro da mesma função que
-- é SECURITY DEFINER (o escopo cross-tenant é só a parte de preço).
DROP FUNCTION IF EXISTS core.network_contract_price_benchmark(UUID, INT, INT);

CREATE FUNCTION core.network_contract_price_benchmark(
    requesting_tenant_id UUID,
    min_cohort INT DEFAULT 3,
    volume_window_days INT DEFAULT 90
)
RETURNS TABLE (
    insurance_plan_id      UUID,
    plan_display_name      TEXT,
    tuss_code              VARCHAR(20),
    procedure_name         TEXT,
    your_price             NUMERIC,
    network_median_price   NUMERIC,
    network_cohort_size    INT,
    monthly_volume         NUMERIC
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = core, pg_temp
STABLE
AS $$
    WITH active_tenant_ids AS (
        SELECT id FROM core.tenants WHERE is_active
    ),
    active_contracts AS (
        SELECT c.id, c.tenant_id, c.insurance_plan_id
        FROM core.contracts c
        JOIN active_tenant_ids act ON act.id = c.tenant_id
        WHERE c.status = 'homologado'
          AND c.valid_from <= CURRENT_DATE
          AND (c.valid_until IS NULL OR c.valid_until >= CURRENT_DATE)
    ),
    active_items AS (
        SELECT
            ac.tenant_id,
            ip.normalized_key,
            ac.insurance_plan_id,
            ci.tuss_code,
            ci.procedure_name,
            ci.agreed_price
        FROM core.contract_items ci
        JOIN active_contracts ac ON ac.id = ci.contract_id
        JOIN core.insurance_plans ip ON ip.id = ac.insurance_plan_id
    ),
    your_items AS (
        SELECT DISTINCT ON (normalized_key, tuss_code)
            insurance_plan_id, normalized_key, tuss_code, procedure_name, agreed_price
        FROM active_items
        WHERE tenant_id = requesting_tenant_id
    ),
    network_medians AS (
        SELECT
            normalized_key,
            tuss_code,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY agreed_price) AS median_price,
            COUNT(DISTINCT tenant_id) AS cohort_size
        FROM active_items
        WHERE tenant_id != requesting_tenant_id
        GROUP BY normalized_key, tuss_code
    ),
    volume AS (
        SELECT
            b.insurance_plan_id,
            a.procedure_code AS tuss_code,
            COUNT(*)::numeric / GREATEST(volume_window_days::numeric / 30.0, 1) AS monthly_volume
        FROM core.billing b
        JOIN core.appointments a ON a.id = b.appointment_id
        WHERE b.tenant_id = requesting_tenant_id
          AND a.procedure_code IS NOT NULL
          AND b.created_at >= now() - (volume_window_days || ' days')::interval
        GROUP BY b.insurance_plan_id, a.procedure_code
    )
    SELECT
        yi.insurance_plan_id,
        ip.display_name,
        yi.tuss_code,
        yi.procedure_name,
        yi.agreed_price,
        nm.median_price,
        nm.cohort_size::int,
        COALESCE(v.monthly_volume, 0)
    FROM your_items yi
    JOIN network_medians nm
        ON nm.normalized_key = yi.normalized_key AND nm.tuss_code = yi.tuss_code
    JOIN core.insurance_plans ip ON ip.id = yi.insurance_plan_id
    LEFT JOIN volume v
        ON v.insurance_plan_id = yi.insurance_plan_id AND v.tuss_code = yi.tuss_code
    WHERE nm.cohort_size >= min_cohort;
$$;

COMMENT ON FUNCTION core.network_contract_price_benchmark IS
  'Oportunidades da Sala de Comando — para cada procedimento que a '
  'clínica solicitante tem homologado com um convênio, devolve o preço '
  'dela + a MEDIANA agregada de outras clínicas para o mesmo '
  'convênio+procedimento (nunca uma linha por clínica), com piso de '
  'amostra mínima por grupo embutido na própria função.';
