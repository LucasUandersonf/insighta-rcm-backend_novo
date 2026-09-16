-- app/sql/039_cost_entries.sql
--
-- Plano Diretor Insighta, épico F3.1 ("Módulo de custos e margem
-- real") — "É o gap mais sério do produto inteiro. ProfitabilityPanel
-- hoje mostra receita por hora ocupada — não margem. Sem custo (folha,
-- aluguel, insumo, repasse), toda conversa de 'rentabilidade' fica
-- pela metade."
--
-- DECISÃO — lançamento MANUAL, mensal, por categoria — não uma regra
-- de comissão percentual automática
-------------------------------------------------------------------------
-- "Comissão/repasse" costuma ser um percentual da receita em muita
-- clínica (profissional PJ, split %), mas modelar isso como REGRA
-- (percentual configurável por profissional, recalculado
-- automaticamente) é um escopo bem maior e mais arriscado de acertar
-- de primeira (qual receita entra na base? bruta ou líquida de glosa?
-- antes ou depois de imposto?) do que pedir pro gestor lançar o valor
-- que de fato foi pago naquele mês — o mesmo princípio de "nunca
-- inventa confiança sem amostra" do resto do motor, aplicado a custo:
-- é melhor um número real digitado do que uma fórmula automática
-- inventada sem validar a hipótese primeiro.
--
-- DECISÃO — rateio por profissional OU geral, nunca "por unidade"
-------------------------------------------------------------------------
-- O Plano Diretor pede "rateio (por profissional, por unidade, ou
-- geral)" — mas consolidação multi-unidade (épico F3.2) ainda não
-- existe neste schema (Tenant HOJE é a própria unidade). "Por
-- unidade" fica pra quando F3.2 existir; até lá, um custo geral já É
-- o custo da unidade inteira (que é o próprio tenant).
--
-- professional_id NULL = custo GERAL da clínica, rateado
-- proporcionalmente à receita de cada profissional no cálculo de
-- margem (ver AnalyticsService.get_profitability) — método simples e
-- documentado, não a única forma correta de ratear overhead, mas a
-- que não exige nenhum dado adicional que o produto não tem hoje.
-- professional_id preenchido = custo DIRETO daquele profissional
-- (ex: repasse individual), carregado 100% nele, sem diluir nos
-- demais.
CREATE TABLE core.cost_entries (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    category        TEXT NOT NULL CHECK (category IN ('folha_fixa', 'comissao_repasse', 'aluguel', 'insumo', 'outros')),
    description     TEXT,
    amount          NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    -- Sempre o dia 1 do mês do custo (ex: 2026-09-01) — mesmo padrão
    -- de health_score_snapshots.snapshot_month: normaliza a
    -- granularidade mensal independente de que dia o lançamento foi
    -- feito.
    period_month    DATE NOT NULL,
    professional_id UUID REFERENCES core.professionals(id) ON DELETE SET NULL,
    created_by      UUID NOT NULL REFERENCES core.users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_cost_entries_tenant_period ON core.cost_entries (tenant_id, period_month);
CREATE INDEX ix_cost_entries_tenant_professional ON core.cost_entries (tenant_id, professional_id) WHERE professional_id IS NOT NULL;

DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT unnest(ARRAY['cost_entries'])
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

COMMENT ON TABLE core.cost_entries IS
  'Lançamento manual de custo mensal por categoria (folha fixa, '
  'comissão/repasse, aluguel, insumo, outros) — alimenta o cálculo de '
  'margem líquida em AnalyticsService.get_profitability. '
  'professional_id NULL = custo geral, rateado por receita entre os '
  'profissionais do período; preenchido = custo direto daquele '
  'profissional.';
