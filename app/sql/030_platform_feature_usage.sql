-- app/sql/030_platform_feature_usage.sql
--
-- Priorização de produto orientada a QUAL RECURSO cada clínica usa, não
-- só "está engajada?" — ver Laudo de Vistoria Técnica (parecer PM/PO):
-- até aqui, o painel de Customer Success (026_platform_customer_success.sql)
-- media ENGAJAMENTO (quantos eventos, quando foi a última atividade), o
-- suficiente pra saber QUEM está em risco de cancelar — mas não dizia
-- nada sobre O QUÊ especificamente cada cliente usa mais, informação que
-- a priorização de backlog vinha sem até agora (decisão direta do PO, o
-- normal antes do primeiro cliente real — ver DECISÃO em
-- platform_reporting_service.py).
--
-- DECISÃO — reaproveita core.audit_log (mutações já registradas) +
-- contagem direta em core.patients/core.appointments, sem tabela nova
-------------------------------------------------------------------------
-- Mesma decisão de 026_platform_customer_success.sql: em vez de
-- instrumentar um evento de "cliquei nesta tela" novo (exigiria mudança
-- em toda tela do frontend + uma tabela de eventos nova só pra isso),
-- reaproveita o que já é escrito de verdade a cada mutação sensível
-- (core.audit_log.entity_type — ver audit_log_repository.py) mais as
-- duas tabelas de domínio que não passam por lá porque nascem de
-- INGESTÃO em massa, não de uma ação isolada de tela (patients/
-- appointments — ver DECISÃO em ingestion.py sobre por que ingestão em
-- massa nunca grava audit_log linha a linha).
--
-- LIMITAÇÃO DELIBERADA, documentada — mede MUTAÇÃO, não NAVEGAÇÃO/LEITURA
-------------------------------------------------------------------------
-- Uma clínica que abre a Sala de Comando todo dia mas nunca edita nada
-- lá aparece com "faturamento"/"agenda" baixos mesmo estando ativa
-- naquela tela — não existe uma chave "sala_de_comando" abaixo, de
-- propósito: não há mutação nenhuma para medir ali. Isto é suficiente
-- para o objetivo de v1 (decidir em qual FRENTE DE ENGENHARIA investir
-- mais — dirigido por AÇÃO real no dado, o sinal mais forte de "isto
-- importa pro cliente"), mas não é telemetria completa de uso de
-- produto; medir navegação exigiria instrumentar o frontend inteiro,
-- deliberadamente fora de escopo desta rodada.
--
-- DECISÃO — sempre as mesmas 6 chaves, mesmo com contagem zero
-------------------------------------------------------------------------
-- jsonb_build_object (não jsonb_strip_nulls) de propósito: o frontend
-- soma esta mesma estrutura entre todos os tenants para montar o
-- ranking "recursos mais usados na plataforma" — uma chave que
-- desaparece quando zerada quebraria essa soma agregada silenciosamente.
DROP FUNCTION IF EXISTS core.platform_tenant_usage_summary();

CREATE FUNCTION core.platform_tenant_usage_summary()
RETURNS TABLE (
    tenant_id             UUID,
    trade_name            VARCHAR,
    plan_tier             VARCHAR,
    tenant_is_active      BOOLEAN,
    tenant_created_at     TIMESTAMPTZ,
    active_users          BIGINT,
    last_activity_at      TIMESTAMPTZ,
    events_last_30d       BIGINT,
    patients_total        BIGINT,
    appointments_last_30d BIGINT,
    billings_last_30d     BIGINT,
    feature_usage_last_30d JSONB
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
        (SELECT COUNT(*) FROM core.billing b WHERE b.tenant_id = t.id AND b.created_at >= now() - interval '30 days') AS billings_last_30d,
        jsonb_build_object(
            'pacientes', (SELECT COUNT(*) FROM core.patients p WHERE p.tenant_id = t.id AND p.created_at >= now() - interval '30 days'),
            'agenda', (SELECT COUNT(*) FROM core.appointments ap WHERE ap.tenant_id = t.id AND ap.created_at >= now() - interval '30 days'),
            'faturamento', (SELECT COUNT(*) FROM core.audit_log a WHERE a.tenant_id = t.id AND a.entity_type = 'billing' AND a.created_at >= now() - interval '30 days'),
            'recurso_de_glosa', (SELECT COUNT(*) FROM core.audit_log a WHERE a.tenant_id = t.id AND a.entity_type = 'denial_appeal' AND a.created_at >= now() - interval '30 days'),
            'contratos', (SELECT COUNT(*) FROM core.audit_log a WHERE a.tenant_id = t.id AND a.entity_type = 'contract' AND a.created_at >= now() - interval '30 days'),
            'usuarios', (SELECT COUNT(*) FROM core.audit_log a WHERE a.tenant_id = t.id AND a.entity_type = 'user' AND a.created_at >= now() - interval '30 days')
        ) AS feature_usage_last_30d
    FROM core.tenants t
    ORDER BY t.trade_name;
$$;

COMMENT ON FUNCTION core.platform_tenant_usage_summary IS
  'Único ponto do sistema autorizado a agregar dado cross-tenant para o '
  'painel interno de Customer Success da própria Insighta. Nunca exposto '
  'a nenhum usuário de clínica — só ao operador da plataforma autenticado '
  'por senha própria (ver app/api/platform_admin_auth.py). '
  'feature_usage_last_30d: decomposição de uso por recurso (mutação, não '
  'navegação — ver DECISÃO no arquivo 030_platform_feature_usage.sql), '
  'usada tanto por clínica quanto somada entre todas para orientar '
  'priorização de backlog.';
