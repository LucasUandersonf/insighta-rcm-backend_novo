-- app/sql/038_executive_narratives.sql
--
-- Resumo executivo narrado por IA (Sala de Comando) — pedido direto do
-- usuário: "a IA seria o Jarvis pegando os nossos cálculos e
-- transformando em texto explicativo... o gestor teria a impressão de
-- um sistema vivo". Esta tabela guarda o texto gerado, uma vez por
-- tenant por DIA (ver DECISÃO em app/services/executive_narrative_service.py),
-- para não chamar a API de IA a cada abertura de tela.
--
-- DECISÃO — cache DIÁRIO, não "a cada abertura"
-------------------------------------------------------------------------
-- Regenerar a narrativa em toda visita à Sala de Comando pareceria
-- "vivo" à primeira vista, mas tem 3 problemas reais: (1) custo
-- multiplicado por cada abertura de cada usuário, sem necessidade —
-- os NÚMEROS por trás só mudam de fato uma vez por dia; (2) latência de
-- rede numa tela que hoje carrega instantaneamente; (3) se o gestor
-- atualiza a página duas vezes em 10 segundos e vê dois textos
-- diferentes sobre os MESMOS números, isso parece bug, não "sistema
-- vivo" — inconsistência quebra confiança mais rápido do que repetição
-- constrói. Uma narrativa por dia já entrega a sensação de novidade
-- (o texto muda todo dia, porque os números mudam todo dia) sem nenhum
-- desses efeitos colaterais.
--
-- DECISÃO — tenant_id + RLS normal (dado da clínica)
-------------------------------------------------------------------------
-- Mesmo raciocínio de health_score_snapshots (034): é o histórico DA
-- CLÍNICA, nunca lido cross-tenant.
CREATE TABLE core.executive_narratives (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    -- Data (fuso do servidor) a que esta narrativa se refere — chave de
    -- cache: uma linha por (tenant_id, digest_date).
    digest_date     DATE NOT NULL,
    -- Janela de dados que alimentou a narrativa (ver DECISÃO no
    -- service: sempre os últimos 7 dias fechados, independente do
    -- seletor de período da tela) — guardado só para transparência na
    -- UI ("baseado nos últimos 7 dias"), nunca recalculado a partir daqui.
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    narrative_text  TEXT NOT NULL,
    -- Qual modelo gerou este texto (EXECUTIVE_NARRATIVE_MODEL no
    -- momento da geração) — histórico de auditoria caso o modelo mude.
    model           TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, digest_date)
);

CREATE INDEX ix_executive_narratives_tenant_date ON core.executive_narratives (tenant_id, digest_date DESC);

DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT unnest(ARRAY['executive_narratives'])
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

COMMENT ON TABLE core.executive_narratives IS
  'Resumo executivo narrado por IA, uma linha por (tenant_id, digest_date) — '
  'cache diário para não chamar a API de IA a cada abertura da Sala de Comando.';
