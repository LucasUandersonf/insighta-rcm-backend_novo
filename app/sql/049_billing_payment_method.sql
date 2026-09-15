-- app/sql/049_billing_payment_method.sql
--
-- "Mapa de Dados Insighta" — Domínio 5/6 (Financeiro particular): tudo
-- que já existe no produto é forte pro lado CONVÊNIO. O paciente
-- particular — cada vez mais relevante conforme coparticipação cresce
-- (ver _coparticipation_growth_insight) — ainda é o lado mais fraco do
-- dado financeiro: sabíamos QUANTO foi cobrado do paciente
-- (coparticipation_value), nunca COMO ele pagou nem em quantas vezes.
-- Alimenta um futuro insight de inadimplência de particular — este
-- arquivo só abre a captura do dado, o insight em si fica pra depois
-- (mesma ordem de "dado primeiro, insight depois" do resto deste mapa).
ALTER TABLE core.billing ADD COLUMN IF NOT EXISTS payment_method VARCHAR(20);
ALTER TABLE core.billing ADD COLUMN IF NOT EXISTS installments SMALLINT;

DO $$ BEGIN
  ALTER TABLE core.billing ADD CONSTRAINT billing_payment_method_check
    CHECK (payment_method IS NULL OR payment_method IN ('dinheiro', 'pix', 'cartao_debito', 'cartao_credito', 'boleto'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  ALTER TABLE core.billing ADD CONSTRAINT billing_installments_check
    CHECK (installments IS NULL OR installments >= 1);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

COMMENT ON COLUMN core.billing.payment_method IS
  'Forma de pagamento da parte PARTICULAR deste faturamento (dinheiro/pix/cartao_debito/cartao_credito/boleto) — NULL quando não informado (todo lançamento existente e toda ingestão que não distingue isso hoje).';
COMMENT ON COLUMN core.billing.installments IS
  'Número de parcelas — só tem sentido de negócio com payment_method=cartao_credito, mas sem CHECK cross-column de propósito (dado de origem nem sempre é consistente; 1 e NULL são tratados como "à vista" pelo frontend de qualquer forma).';
