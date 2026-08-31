-- =====================================================================
-- ARQUIVO: 008_denial_appeals.sql
-- Recurso de Glosa (conformidade ANS) — a segunda metade do ciclo de
-- glosa que o produto ainda não cobria.
--
-- DECISÃO — por que isto é uma entidade NOVA, não um campo a mais em
-- `billing`
-- -------------------------------------------------------------------
-- `denial_risk_engine.py` (já existente) cobre a glosa TÉCNICA: erro de
-- preenchimento (CID/código de procedimento ausente, valor fora da
-- tabela) detectado ANTES do envio à operadora — é prevenção, roda em
-- milissegundos, nunca produz um "processo" com prazo.
--
-- Isto aqui é outra coisa: a NEGATIVA FORMAL que a operadora devolve
-- DEPOIS de já ter recebido a guia — seja por glosa administrativa
-- (documental) seja por negativa médica (cobertura). Isso abre um
-- expediente de contestação com prazo, que pode ser deferido, indeferido,
-- e — se indeferido e o caso envolver direito do beneficiário — escalado
-- para NIP junto à ANS. É um sub-processo com estado e tempo, não um
-- campo booleano em `billing`. Por isso vive em tabela própria,
-- referenciando `billing` (o lote/atendimento que originou a negativa),
-- não o contrário.
--
-- DECISÃO — prazo é CONTRATUAL, não uma lei federal única (ver
-- DEFAULT_APPEAL_DEADLINE_DAYS em app/core/config.py) — por isso
-- `insurance_companies.default_appeal_deadline_days` é configurável por
-- operadora, e `denial_appeals.deadline_at` pode ser sobrescrito caso a
-- caso (o prazo real está escrito no contrato, o sistema só ajuda a não
-- esquecer).
-- =====================================================================

ALTER TABLE core.insurance_companies
    ADD COLUMN IF NOT EXISTS default_appeal_deadline_days INT;

COMMENT ON COLUMN core.insurance_companies.default_appeal_deadline_days IS
  'Prazo padrão (em dias corridos) para contestar uma glosa desta operadora, contado a partir de denial_appeals.denied_at. Configurado pelo tenant a partir do contrato real — NULL usa o fallback genérico DEFAULT_APPEAL_DEADLINE_DAYS.';

CREATE TABLE core.denial_appeals (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    billing_id          UUID NOT NULL REFERENCES core.billing(id) ON DELETE CASCADE,

    -- 'tecnica' aqui é o caso raro de uma negativa formal que a
    -- operadora justificou como erro de preenchimento MESMO DEPOIS do
    -- billing já ter passado pelo denial_risk_engine (falso negativo do
    -- motor, ou regra da operadora que o motor ainda não conhece) —
    -- 'administrativa' é negativa documental, 'medica' é negativa de
    -- cobertura/pertinência.
    appeal_type         VARCHAR(20) NOT NULL
                        CHECK (appeal_type IN ('tecnica', 'administrativa', 'medica')),

    operator_denial_reason TEXT,
    denied_at           DATE NOT NULL,
    deadline_at         DATE NOT NULL,

    -- aberto: negativa registrada, recurso ainda não protocolado.
    -- protocolado: recurso já enviado à operadora, aguardando resposta.
    -- deferido / indeferido: resposta final da operadora nesta instância.
    -- nip_aberta: indeferido e o caso foi escalado como NIP na ANS
    -- (só faz sentido para negativa que afeta o beneficiário, não toda
    -- glosa administrativa entre clínica e operadora — a decisão de
    -- escalar é humana, o sistema só registra o estado).
    status              VARCHAR(20) NOT NULL DEFAULT 'aberto'
                        CHECK (status IN ('aberto', 'protocolado', 'deferido', 'indeferido', 'nip_aberta')),

    filed_at            TIMESTAMPTZ,
    resolution_notes    TEXT,
    resolved_at         TIMESTAMPTZ,

    created_by          UUID REFERENCES core.users(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_denial_appeals_billing ON core.denial_appeals (billing_id);
-- Sustenta a consulta "prazos vencendo em N dias" (tela de alertas e
-- KPI de executive-summary) sem varrer a tabela inteira: filtra por
-- tenant (RLS já cobre, mas o índice físico ainda ajuda) + status aberto
-- + ordenação por deadline.
CREATE INDEX ix_denial_appeals_deadline ON core.denial_appeals (tenant_id, deadline_at)
    WHERE status IN ('aberto', 'protocolado');

CREATE TABLE core.denial_appeal_attachments (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    appeal_id           UUID NOT NULL REFERENCES core.denial_appeals(id) ON DELETE CASCADE,
    s3_key              VARCHAR(512) NOT NULL,
    filename            VARCHAR(255) NOT NULL,
    uploaded_by         UUID REFERENCES core.users(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_denial_appeal_attachments_appeal ON core.denial_appeal_attachments (appeal_id);

DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT unnest(ARRAY['denial_appeals', 'denial_appeal_attachments'])
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
