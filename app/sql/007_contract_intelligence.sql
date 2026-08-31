-- =====================================================================
-- ARQUIVO: 007_contract_intelligence.sql
-- Parser Inteligente de Contratos (IA) — hierarquia Convênio -> Plano ->
-- Contrato -> Itens, e os dois lados do "buraco financeiro" (cobrança
-- abaixo do contratado E repasse abaixo do contratado).
--
-- DECISÃO — por que quebrar "contracts" (que antes carregava
-- procedure_code/agreed_value direto na linha) em contracts (cabeçalho)
-- + contract_items (tabela de preços)
-- -------------------------------------------------------------------
-- Um contrato de convênio real tem UMA vigência e UM PDF de origem, mas
-- CENTENAS de códigos TUSS com preços diferentes. Modelar isso como uma
-- linha por código (o esquema antigo) obrigava a repetir vigência/PDF em
-- cada linha e não tinha onde pendurar o resultado da extração por IA
-- (status de homologação, quem revisou, quando). O cabeçalho agora é o
-- contrato em si; contract_items é a tabela de preços granular que o
-- Parser Inteligente popula (depois de revisão humana — nunca direto).
--
-- Como o produto NUNCA foi implantado em produção (ver
-- STATUS_DO_PROJETO.md original — "nenhum usuário real usou isso"), esta
-- é uma migração estrutural limpa, não uma migração de dado real: não
-- há linha de core.contracts em produção para preservar.
-- =====================================================================

-- ---------------------------------------------------------------------
-- TABELA: insurance_companies (Operadoras — Amil, Bradesco, Unimed...)
-- ---------------------------------------------------------------------
CREATE TABLE core.insurance_companies (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    name        VARCHAR(255) NOT NULL,
    ans_registry VARCHAR(20),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

-- insurance_plans passa a pertencer a uma operadora. NULLABLE de
-- propósito: convênios cadastrados antes desta feature (ou importados
-- sem operadora reconhecida) continuam funcionando — a tela de
-- Convênios passa a EXIGIR operadora para cadastro NOVO, mas o backend
-- não quebra registro antigo. Ver DECISÃO em app/schemas/insurance_plan.py.
ALTER TABLE core.insurance_plans
    ADD COLUMN IF NOT EXISTS insurance_company_id UUID REFERENCES core.insurance_companies(id);

-- ---------------------------------------------------------------------
-- contracts: de "uma linha de preço" para "cabeçalho de vigência + PDF"
-- ---------------------------------------------------------------------
ALTER TABLE core.contracts
    DROP COLUMN IF EXISTS procedure_code,
    DROP COLUMN IF EXISTS procedure_desc,
    DROP COLUMN IF EXISTS agreed_value,
    ADD COLUMN IF NOT EXISTS pdf_s3_key VARCHAR(512),
    -- rascunho: PDF subiu, extração ainda não rodou ou ainda não foi revisada.
    -- em_revisao: IA já extraiu, aguardando o faturista bater o olho (Tela C).
    -- homologado: revisado por humano e salvo em contract_items — só
    -- contratos homologados entram no motor de glosa/analytics.
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'homologado',
    ADD COLUMN IF NOT EXISTS extracted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS homologated_by UUID REFERENCES core.users(id),
    ADD COLUMN IF NOT EXISTS homologated_at TIMESTAMPTZ;

COMMENT ON COLUMN core.contracts.status IS
  'rascunho -> em_revisao -> homologado. Só homologado alimenta denial_risk_engine e analytics — um contrato em revisão não pode silenciosamente virar "a verdade" sem humano confirmar.';

-- ---------------------------------------------------------------------
-- TABELA: contract_items (a tabela de preços em si — o que o Parser de
-- IA extrai do PDF e o faturista homologa)
-- ---------------------------------------------------------------------
CREATE TABLE core.contract_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    contract_id     UUID NOT NULL REFERENCES core.contracts(id) ON DELETE CASCADE,
    tuss_code       VARCHAR(20) NOT NULL,
    procedure_name  VARCHAR(255),
    agreed_price    NUMERIC(12, 2) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Um código TUSS aparece uma vez por contrato — evita duas linhas
    -- concorrentes pro mesmo procedimento dentro do MESMO PDF (a IA ou o
    -- humano tem que escolher qual preço vale, não o banco silenciosamente).
    UNIQUE (contract_id, tuss_code)
);

CREATE INDEX ix_contract_items_lookup ON core.contract_items (tenant_id, tuss_code);

-- ---------------------------------------------------------------------
-- billing: os dois lados do cruzamento passam a existir na mesma linha
-- -------------------------------------------------------------------
-- charged_value (já existia) = o que a CLÍNICA cobrou.
-- received_value (novo) = o que a OPERADORA efetivamente repassou, só
-- preenchido quando o lote é liquidado (settle_billing em
-- billing_service.py) — nasce NULL porque "ainda não sei quanto vou
-- receber" é um estado real, não é 0 (mesmo princípio de sempre: ausência
-- de dado não é zero).
-- ---------------------------------------------------------------------
ALTER TABLE core.billing
    ADD COLUMN IF NOT EXISTS received_value NUMERIC(12, 2),
    ADD COLUMN IF NOT EXISTS settled_at TIMESTAMPTZ;

COMMENT ON COLUMN core.billing.received_value IS
  'Valor efetivamente repassado pela operadora na liquidação do lote. NULL = ainda não liquidado. Comparado contra contract_items.agreed_price para detectar underpayment (Divergência de Recebimento).';

-- ---------------------------------------------------------------------
-- RLS nas tabelas novas — mesmo padrão de sempre.
-- ---------------------------------------------------------------------
DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT unnest(ARRAY['insurance_companies', 'contract_items'])
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
