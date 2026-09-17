-- app/sql/042_organizations.sql
--
-- Épico F3.2 do Plano Diretor ("Consolidação multi-unidade") — conceito
-- de Organization agrupando múltiplos Tenant + dashboard consolidado
-- comparando unidades lado a lado.
--
-- DECISÃO — organization_id é NULLABLE, atribuído por ops (MESMO padrão
-- de provisionamento de tenant que já existe hoje)
-------------------------------------------------------------------------
-- Não existe hoje nenhum cadastro público/self-service de tenant (ver
-- DECISÃO em app/scripts/create_admin.py) — toda clínica nova entra na
-- base via script de operação. Agrupar clínicas do MESMO dono numa
-- Organization é a mesma categoria de decisão (uma relação comercial
-- multi-unidade, não uma escolha self-service de dentro do produto) —
-- por isso o agrupamento é feito pelo MESMO script (ver
-- --organization-name em create_admin.py), não por uma tela nova de
-- "criar organização" dentro do produto. A maioria dos tenants continua
-- com organization_id NULL (clínica avulsa, sem consolidação) — o
-- estado normal, nunca tratado como "faltando configurar".
--
-- DECISÃO — dashboard consolidado é NÃO anonimizado, ao contrário do
-- Comparativo entre Clínicas (032_network_benchmark.sql)
-------------------------------------------------------------------------
-- O Comparativo entre Clínicas nunca devolve uma linha por concorrente
-- (privacidade entre donos diferentes). Aqui é o OPOSTO: as unidades de
-- uma Organization pertencem ao MESMO dono — o valor do produto é
-- justamente comparar "Unidade Centro" contra "Unidade Zona Sul" lado a
-- lado, linha por linha. A proteção aqui não é anonimização, é RBAC: só
-- owner/admin (ver app/api/v1/endpoints/analytics.py) enxergam o
-- consolidado, e a função só devolve linhas de unidades da MESMA
-- organization_id do tenant solicitante (nunca de uma organização
-- diferente).
CREATE TABLE IF NOT EXISTS core.organizations (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE core.tenants ADD COLUMN IF NOT EXISTS organization_id UUID REFERENCES core.organizations(id);
CREATE INDEX IF NOT EXISTS idx_tenants_organization_id ON core.tenants (organization_id) WHERE organization_id IS NOT NULL;

COMMENT ON TABLE core.organizations IS
  'Épico F3.2 do Plano Diretor — agrupa Tenants do MESMO dono para o dashboard consolidado multi-unidade. Atribuído por ops (ver create_admin.py), não self-service.';
COMMENT ON COLUMN core.tenants.organization_id IS
  'NULL = clínica avulsa (estado normal, sem consolidação). Preenchido = pertence a um grupo multi-unidade — ver GET /analytics/organization-summary.';

-- Função agregadora cross-tenant — MESMO padrão SECURITY DEFINER de
-- 032_network_benchmark.sql, role PRÓPRIA (organization_reporting_owner,
-- ver DECISÃO no bootstrap_db.py): categoria de dado diferente das
-- outras (não anonimizada, escopada por organization_id, não "toda a
-- rede"). Devolve UMA LINHA POR UNIDADE da MESMA organização do tenant
-- solicitante (incluindo ele mesmo) — nunca de organização diferente.
DROP FUNCTION IF EXISTS core.organization_units_summary(UUID, INT);

CREATE FUNCTION core.organization_units_summary(
    requesting_tenant_id UUID,
    window_days INT DEFAULT 30
)
RETURNS TABLE (
    tenant_id            UUID,
    trade_name           VARCHAR,
    organization_name    VARCHAR,
    is_requesting_tenant BOOLEAN,
    total_billed         NUMERIC,
    denial_risk_value    NUMERIC,
    appointment_count    INT,
    no_show_count        INT,
    no_show_total        INT
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = core, pg_temp
STABLE
AS $$
    WITH requesting_org AS (
        SELECT organization_id FROM core.tenants WHERE id = requesting_tenant_id
    ),
    org_tenants AS (
        SELECT t.id, t.trade_name, o.name AS organization_name
        FROM core.tenants t
        JOIN core.organizations o ON o.id = t.organization_id
        JOIN requesting_org ro ON ro.organization_id = t.organization_id
        WHERE t.is_active
    ),
    bounds AS (
        SELECT now() - (window_days || ' days')::interval AS start_ts
    ),
    billing_agg AS (
        SELECT
            b.tenant_id,
            SUM(b.charged_value) AS total_billed,
            SUM(CASE WHEN b.denial_risk_level IN ('medium', 'high') THEN b.charged_value ELSE 0 END) AS denial_risk_value
        FROM core.billing b, bounds
        WHERE b.created_at >= bounds.start_ts
        GROUP BY b.tenant_id
    ),
    appointment_agg AS (
        SELECT
            a.tenant_id,
            COUNT(*) AS appointment_count,
            SUM(CASE WHEN a.status = 'no_show' THEN 1 ELSE 0 END) AS no_show_count,
            SUM(CASE WHEN a.status IN ('completed', 'no_show') THEN 1 ELSE 0 END) AS no_show_total
        FROM core.appointments a, bounds
        WHERE a.scheduled_at >= bounds.start_ts
        GROUP BY a.tenant_id
    )
    SELECT
        ot.id,
        ot.trade_name,
        ot.organization_name,
        (ot.id = requesting_tenant_id),
        COALESCE(ba.total_billed, 0),
        COALESCE(ba.denial_risk_value, 0),
        COALESCE(aa.appointment_count, 0)::int,
        COALESCE(aa.no_show_count, 0)::int,
        COALESCE(aa.no_show_total, 0)::int
    FROM org_tenants ot
    LEFT JOIN billing_agg ba ON ba.tenant_id = ot.id
    LEFT JOIN appointment_agg aa ON aa.tenant_id = ot.id
    ORDER BY ot.trade_name;
$$;

COMMENT ON FUNCTION core.organization_units_summary IS
  'Dashboard consolidado multi-unidade (Épico F3.2) — uma linha por Tenant da MESMA organization_id do solicitante (nunca de outra organização), com faturamento/risco de glosa/falta no período.';
