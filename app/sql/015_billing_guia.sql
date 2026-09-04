-- =====================================================================
-- ARQUIVO: 015_billing_guia.sql
-- Guia (TISS) — Fase 1 do plano de adequação ao fluxo real de mercado
-- (Agendamento -> Atendimento -> Faturamento, achado avaliando o ERP
-- Moderna e o padrão ANS/TISS: ver conversa e MODERNANET_REFERENCIA.md).
--
-- DECISÃO — Guia como TABELA PRÓPRIA, 1:N com Billing (não campos soltos
-- na própria Billing)
-- -------------------------------------------------------------------
-- Uma guia SP/SADT real pode conter VÁRIOS procedimentos (itens) —
-- exatamente como o "Anexo de Outras Despesas" da Moderna mostra: uma
-- guia, uma tabela de itens embaixo. Modelar como campos soltos em
-- Billing assumiria 1 Billing = 1 Guia sempre, o que quebraria no
-- primeiro caso real de guia com múltiplos itens. `Billing.guia_id`
-- (nullable) é quem liga N linhas de Billing a 1 Guia.
--
-- DECISÃO — guia_id é NULLABLE em billing
-- -------------------------------------------------------------------
-- Todo Billing criado pela ingestão em massa de CSV/XML/JSON hoje não
-- tem noção de guia (o formato de arquivo não carrega essa informação
-- — ainda não temos confirmado o layout real de export de nenhum ERP
-- com esse dado). Exigir guia_id quebraria 100% do fluxo de ingestão
-- existente. A guia é OPCIONAL, preenchida quando o faturista de fato
-- gera/recebe a guia (fluxo manual, ou futura integração TISS real).
--
-- DECISÃO — só os 4 tipos oficiais do padrão TISS/ANS
-- -------------------------------------------------------------------
-- Confirmado tanto na tela "Gera Arquivo - TISS" da Moderna (checkboxes
-- Consulta/Honorários/SADT/Resumo Internação) quanto na documentação
-- pública da ANS (Padrão TISS — Componente Organizacional): são
-- exatamente 4 tipos de guia, sem variação por operadora.
-- =====================================================================

CREATE TABLE core.guias (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    insurance_plan_id    UUID NOT NULL REFERENCES core.insurance_plans(id),
    tipo                 VARCHAR(30) NOT NULL,
    -- Número da guia — atribuído pelo prestador (rascunho) ou pela
    -- operadora (após autorização). NULLABLE: uma guia pode ser criada
    -- antes de ter número definitivo.
    numero               VARCHAR(50),
    -- Senha de autorização + validade — só existe quando o
    -- procedimento exigiu autorização prévia da operadora (nem todo
    -- atendimento exige; consulta simples tipicamente não).
    senha                VARCHAR(50),
    senha_validade       DATE,
    -- Código da tabela de procedimento usada nesta guia (padrão TISS:
    -- 18=CBHPM, 19/20=tabela própria do prestador/operadora, 22=TUSS).
    -- NULLABLE porque nem toda guia tem isso confirmado no momento do
    -- cadastro.
    tabela_procedimento  VARCHAR(5),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT guias_tipo_check CHECK (tipo IN ('consulta', 'sadt', 'resumo_internacao', 'honorario'))
);

CREATE INDEX ix_guias_tenant_plan ON core.guias (tenant_id, insurance_plan_id);

ALTER TABLE core.billing
    ADD COLUMN guia_id UUID REFERENCES core.guias(id);

COMMENT ON COLUMN core.billing.guia_id IS
  'Guia TISS à qual este lançamento pertence — NULL para todo billing vindo da ingestão em massa hoje (o formato de arquivo ainda não carrega essa informação). Uma guia pode agrupar N linhas de billing (ex.: SADT com vários procedimentos).';

CREATE INDEX ix_billing_guia_id ON core.billing (guia_id) WHERE guia_id IS NOT NULL;

-- ---------------------------------------------------------------------
-- RLS na tabela nova — mesmo padrão de sempre.
-- ---------------------------------------------------------------------
DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT unnest(ARRAY['guias'])
    LOOP
        EXECUTE format('ALTER TABLE core.%I ENABLE ROW LEVEL SECURITY;', t);
        EXECUTE format('ALTER TABLE core.%I FORCE ROW LEVEL SECURITY;', t);
        EXECUTE format($f$
            CREATE POLICY tenant_isolation_%1$I ON core.%1$I
            USING (tenant_id = core.current_tenant_id())
            WITH CHECK (tenant_id = core.current_tenant_id());
        $f$, t);
    END LOOP;
END $$;
