-- =====================================================================
-- ARQUIVO: 016_lotes_faturas.sql
-- Lote + Fatura — Fase 2 do plano de adequação ao fluxo real de mercado
-- (Agendamento -> Atendimento -> Faturamento). Ver DECISÃO completa em
-- app/sql/015_billing_guia.sql (Fase 1 — Guia) e MODERNANET_REFERENCIA.md/
-- PLANO_ADEQUACAO_TISS.md (conversa com o usuário).
--
-- DECISÃO — Lote agrupa Guias do MESMO convênio + MESMO tipo
-- -------------------------------------------------------------------
-- Confirmado de forma idêntica em 3 ERPs independentes do mercado
-- brasileiro (Moderna, Feegow, iClinic — ver pesquisa na conversa):
-- um lote só pode conter guias de UM convênio e UM tipo (Consulta/
-- SADT/Resumo Internação/Honorário). Feegow e iClinic confirmam ainda
-- o limite de até 100 guias por lote — regra do próprio padrão TISS
-- para o tamanho do arquivo XML. Não aplicamos esse limite AQUI porque
-- ainda não geramos XML TISS de verdade (Fase 5, deliberadamente
-- adiada) — é uma restrição de formato de arquivo, não de negócio.
--
-- DECISÃO — ciclo de vida do Lote: aberto -> fechado -> faturado
-- -------------------------------------------------------------------
-- "aberto": guias podem ser adicionadas/removidas livremente (equivale
-- ao "Atribuir ao Lote"/seleção de pacientes da Moderna).
-- "fechado": o "Bloquear" da Moderna — trava edição, pronto para virar
-- arquivo/fatura. Não pode ficar vazio (sem sentido fechar um lote sem
-- nenhuma guia).
-- "faturado": entrou em uma Fatura — nunca mais editável.
--
-- DECISÃO — Fatura tem vida própria além dos Lotes que agrupa
-- -------------------------------------------------------------------
-- Uma Fatura pode agrupar MAIS DE UM lote (ex.: lotes de tipos
-- diferentes do mesmo convênio, fechados na mesma janela de envio —
-- ver "Faturamento Emitido"/"Gera Arquivo - TISS" da Moderna, que
-- tratam Lote e Fatura como seleções independentes). A baixa
-- (recebimento) acontece no nível da FATURA, não do lote — é a fatura
-- que a operadora paga.
-- =====================================================================

CREATE TABLE core.faturas (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    insurance_plan_id   UUID NOT NULL REFERENCES core.insurance_plans(id),
    -- Série + número — mesma numeração fiscal que a tela "Faturamento
    -- Emitido" da Moderna mostra (multi-seleção de "Série-Fatura": TS,
    -- NF, SA, 2, I, NA...). NULLABLE: uma fatura pode existir em
    -- rascunho antes de receber numeração definitiva.
    serie               VARCHAR(10),
    numero              VARCHAR(30),
    status              VARCHAR(20) NOT NULL DEFAULT 'emitida',
    data_emissao        TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Preenchidos só na baixa (settle_fatura) — mesmo princípio de
    -- Billing.received_value/settled_at: NULL = ainda não recebido,
    -- nunca 0.
    valor_recebido      NUMERIC(12, 2),
    data_recebimento    TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT faturas_status_check CHECK (status IN ('emitida', 'paga', 'parcialmente_paga', 'cancelada'))
);

CREATE INDEX ix_faturas_tenant_plan ON core.faturas (tenant_id, insurance_plan_id);

CREATE TABLE core.lotes (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    insurance_plan_id   UUID NOT NULL REFERENCES core.insurance_plans(id),
    tipo                VARCHAR(30) NOT NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'aberto',
    fatura_id           UUID REFERENCES core.faturas(id),
    closed_at           TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT lotes_tipo_check CHECK (tipo IN ('consulta', 'sadt', 'resumo_internacao', 'honorario')),
    CONSTRAINT lotes_status_check CHECK (status IN ('aberto', 'fechado', 'faturado'))
);

CREATE INDEX ix_lotes_tenant_plan ON core.lotes (tenant_id, insurance_plan_id);
CREATE INDEX ix_lotes_fatura_id ON core.lotes (fatura_id) WHERE fatura_id IS NOT NULL;

-- Guia passa a poder pertencer a um lote (ver DECISÃO acima) —
-- NULLABLE: uma guia recém-criada ainda não está em nenhum lote.
ALTER TABLE core.guias
    ADD COLUMN lote_id UUID REFERENCES core.lotes(id);

COMMENT ON COLUMN core.guias.lote_id IS
  'Lote ao qual esta guia foi atribuída (ver core.lotes) — NULL até o faturista atribuí-la a um lote. Uma guia só pode estar em UM lote por vez.';

CREATE INDEX ix_guias_lote_id ON core.guias (lote_id) WHERE lote_id IS NOT NULL;

-- ---------------------------------------------------------------------
-- RLS nas tabelas novas — mesmo padrão de sempre.
-- ---------------------------------------------------------------------
DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT unnest(ARRAY['faturas', 'lotes'])
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
