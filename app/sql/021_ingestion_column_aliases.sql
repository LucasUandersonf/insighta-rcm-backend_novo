-- app/sql/021_ingestion_column_aliases.sql
--
-- Mapeador automático de coluna — achado do usuário sobre lacuna do
-- produto: hoje o cliente precisa exportar o CSV com o cabeçalho EXATO
-- que o template exige (ver TEMPLATE_FATURAMENTO.md/TEMPLATE_AGENDA.md);
-- se o ERP de origem chama a coluna de outro jeito ("CPF_PAC" em vez de
-- "cpf_paciente"), TODA linha do arquivo é rejeitada por campo
-- obrigatório ausente, sem nenhuma pista clara do motivo.
--
-- DECISÃO — mapeamento é por TENANT + data_type, aprendido UMA VEZ
-- -------------------------------------------------------------------
-- Um cliente sempre exporta do MESMO sistema, com o MESMO cabeçalho —
-- não faz sentido pedir para ele mapear de novo a cada upload. Depois
-- que o mapeamento é confirmado (ver app/services/column_mapping_service.py
-- e POST /ingestion/column-aliases), toda importação futura desse tenant
-- para aquele template já aplica sozinha.
--
-- DECISÃO — só resolve HEADER -> CAMPO CANÔNICO, nunca dado de linha
-- -------------------------------------------------------------------
-- Isso é diferente de InsurancePlanAlias (que aprende variação de VALOR
-- de uma célula, "UNIMED NAC." -> um convênio já cadastrado). Aqui é
-- estrutural: qual COLUNA do arquivo corresponde a qual campo do nosso
-- schema — decidido uma vez por integração, não por linha.
--
-- Escopo desta primeira versão: só o parser CSV de Faturamento consome
-- esta tabela (ver csv_parser.py) — é onde cada campo canônico já é um
-- passthrough 1:1 de uma coluna. O parser de Agenda tem duas colunas
-- (data + hora) compondo um único campo (scheduled_at), o que exigiria
-- uma UI de mapeamento mais complexa (many-to-one); fica para quando um
-- cliente concreto de Agenda precisar, mesmo critério incremental já
-- usado no resto do projeto.
CREATE TABLE core.ingestion_column_aliases (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    data_type       VARCHAR(20) NOT NULL CHECK (data_type IN ('faturamento', 'agenda')),
    source_header   VARCHAR(255) NOT NULL,   -- cabeçalho EXATO como aparece no arquivo do cliente
    canonical_field VARCHAR(50) NOT NULL,    -- nome do campo canônico (ex: "patient_name")
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Um mesmo cabeçalho de origem só pode apontar para UM campo canônico
    -- por tenant+template — reenviar o mesmo mapeamento é um UPSERT
    -- (ver IngestionColumnAliasRepository.save_many), não uma duplicata.
    UNIQUE (tenant_id, data_type, source_header)
);

CREATE INDEX ix_ingestion_column_aliases_lookup ON core.ingestion_column_aliases (tenant_id, data_type);

DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT unnest(ARRAY['ingestion_column_aliases'])
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
