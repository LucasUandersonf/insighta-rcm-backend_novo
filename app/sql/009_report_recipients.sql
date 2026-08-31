-- =====================================================================
-- ARQUIVO: 009_report_recipients.sql
-- Cadastro de MÚLTIPLOS destinatários de relatório por tenant.
--
-- DECISÃO — por que isto é uma entidade NOVA, não repetir
-- `tenants.whatsapp_group_id`
-- -------------------------------------------------------------------
-- Até agora (ver app/worker/weekly_report_job.py e
-- app/services/whatsapp_client.py) cada tenant tinha exatamente UM
-- número de destino gravado direto em `core.tenants`. O produto agora
-- precisa mandar o mesmo relatório (ou relatórios diferentes) para
-- VÁRIAS pessoas da clínica — sócio, gerente financeiro, auditor
-- externo — cada uma possivelmente interessada só em um subconjunto de
-- relatórios. Por isso vira tabela própria (N destinatários por
-- tenant), não uma segunda/terceira coluna em `tenants`.
--
-- DECISÃO — `report_types` como TEXT[] em vez de tabela de junção
-- -------------------------------------------------------------------
-- O conjunto de "tipos de relatório" hoje é pequeno e não tem cadastro
-- próprio (não existe uma tabela `report_types` no sistema — ver
-- app/worker/weekly_report_job.py, que só conhece "relatório semanal").
-- Um array de texto evita criar uma tabela de catálogo + tabela de
-- junção para um conjunto de valores que hoje é essencialmente um
-- enum livre. Array VAZIO ('{}', o default) é o curinga "todos os
-- relatórios" — não precisa listar every tipo existente hoje para
-- continuar recebendo tipos criados no futuro.
--
-- DECISÃO — pelo menos um contato (phone OU email) via CHECK
-- -------------------------------------------------------------------
-- Um destinatário sem nenhuma forma de contato é um cadastro inútil —
-- melhor rejeitar na constraint do banco (defesa em profundidade, além
-- da validação já feita em app/services/report_recipient_service.py)
-- do que permitir um registro "morto" que nunca recebe nada.
-- =====================================================================

CREATE TABLE core.report_recipients (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,

    name                TEXT NOT NULL,
    phone_whatsapp      TEXT,
    email               TEXT,

    -- '{}' (vazio) = recebe TODOS os tipos de relatório existentes hoje
    -- e os que vierem a existir. Uma lista não-vazia restringe o
    -- destinatário aos tipos nela listados (ex: '{weekly_summary}').
    report_types        TEXT[] NOT NULL DEFAULT '{}',

    active              BOOLEAN NOT NULL DEFAULT true,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_report_recipients_has_contact
        CHECK (phone_whatsapp IS NOT NULL OR email IS NOT NULL)
);

CREATE INDEX ix_report_recipients_tenant ON core.report_recipients (tenant_id);
-- Sustenta a consulta "destinatários ativos deste tipo de relatório"
-- (list_for_report_type, usada pelo fan-out do worker semanal) sem
-- varrer destinatários inativos.
CREATE INDEX ix_report_recipients_tenant_active ON core.report_recipients (tenant_id, active)
    WHERE active = true;

ALTER TABLE core.report_recipients ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.report_recipients FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_report_recipients ON core.report_recipients
    USING (tenant_id = core.current_tenant_id())
    WITH CHECK (tenant_id = core.current_tenant_id());
