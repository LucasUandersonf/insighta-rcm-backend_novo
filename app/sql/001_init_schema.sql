-- =====================================================================
-- ARQUIVO: 001_init_schema.sql
-- PROJETO: RCM/ERP Médico SaaS (Multi-tenant B2B - HealthTech)
-- OBJETIVO: Schema inicial + isolamento de dados via Row-Level Security
--
-- DECISÃO ARQUITETURAL #1 — Estratégia de Multi-tenancy
-- ---------------------------------------------------------------------
-- Existem 3 estratégias clássicas de multi-tenancy em Postgres:
--   (a) Database por tenant       -> isolamento máximo, custo operacional altíssimo
--       (migrations, backups e conexões multiplicados por N clínicas).
--   (b) Schema por tenant         -> isolamento bom, mas migrations e pool de
--       conexões (pgbouncer) ficam complexos acima de algumas centenas de tenants.
--   (c) Linha compartilhada + RLS -> um único schema, uma coluna tenant_id em
--       cada tabela, e o próprio Postgres barra o acesso cruzado no nível de
--       linha, independentemente de bug de aplicação.
--
-- Escolhemos (c) porque: o volume de clínicas tende a crescer rápido (SaaS
-- self-service), queries agregadas cross-tenant (analytics interno, billing,
-- suporte) ficam triviais, e o RLS nos dá uma "segunda trava" de segurança
-- que funciona mesmo se um desenvolvedor esquecer um WHERE tenant_id = ...
-- em algum endpoint do FastAPI. Isso é crítico em HealthTech: um vazamento
-- de dados de paciente entre clínicas é um incidente grave de LGPD/HIPAA-like.
-- =====================================================================

-- Extensões necessárias
CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "pg_trgm";    -- busca fuzzy (nomes de pacientes/convênios)
CREATE EXTENSION IF NOT EXISTS "citext";     -- tipo de texto case-insensitive (users.email) — precisa
                                              -- vir ANTES de qualquer CREATE TABLE que use CITEXT

-- ---------------------------------------------------------------------
-- SCHEMA
-- ---------------------------------------------------------------------
-- DECISÃO #2 — Por que um schema dedicado "core" em vez de "public"
-- Manter tudo fora de "public" facilita dar permissões diferenciadas por
-- schema (ex: um schema "analytics" só-leitura para BI, um "audit" para
-- logs) sem misturar tudo no mesmo namespace. Também facilita o versionamento
-- de migrations (Alembic) com um search_path previsível.
CREATE SCHEMA IF NOT EXISTS core;
SET search_path TO core, public;


-- =====================================================================
-- TABELA: tenants
-- Representa cada clínica/hospital cliente da plataforma (o "locatário").
-- Toda tabela operacional do sistema terá uma FK para tenants.id.
-- =====================================================================
CREATE TABLE core.tenants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    legal_name      VARCHAR(255)  NOT NULL,
    trade_name      VARCHAR(255)  NOT NULL,
    cnpj            VARCHAR(18)   NOT NULL UNIQUE,
    plan_tier       VARCHAR(50)   NOT NULL DEFAULT 'starter'
                        CHECK (plan_tier IN ('starter','pro','enterprise')),
    whatsapp_group_id  VARCHAR(100),      -- ID do grupo/número corporativo p/ Etapa 4
    is_active       BOOLEAN       NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ   NOT NULL DEFAULT now()
);

COMMENT ON TABLE core.tenants IS
  'Cada linha = uma clínica/hospital cliente (tenant). É a raiz da árvore de isolamento.';


-- =====================================================================
-- TABELA: users (usuários da plataforma, ligados a um tenant)
--
-- DECISÃO #3 — RBAC via coluna "role" + tabela de permissões futura
-- Para o MVP, um enum de papéis fixos (owner, admin, financeiro, atendimento,
-- auditor) resolve 90% dos casos e é simples de raciocinar no RLS e nas
-- rotas do FastAPI (Depends(require_role("admin"))). Se o produto crescer
-- para permissões granulares por tela, migramos para um modelo
-- roles <-> permissions (many-to-many) sem quebrar a coluna "role" (ela vira
-- um "papel padrão" herdado). Não fazemos essa complexidade agora: YAGNI.
-- =====================================================================
CREATE TYPE core.user_role AS ENUM (
    'owner',        -- diretoria/dono da clínica - acesso total ao tenant
    'admin',        -- gestor operacional
    'financeiro',   -- acesso a faturamento e glosas
    'atendimento',  -- acesso a agenda/pacientes, sem financeiro sensível
    'auditor'       -- somente leitura (ex: contador externo)
);

CREATE TABLE core.users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    email           CITEXT NOT NULL,          -- CITEXT = case-insensitive, evita duplicidade "A@x.com" vs "a@x.com"
    hashed_password VARCHAR(255) NOT NULL,     -- nunca armazenar senha em texto puro; bcrypt/argon2 na app
    full_name       VARCHAR(255) NOT NULL,
    role            core.user_role NOT NULL DEFAULT 'atendimento',
    is_active       BOOLEAN NOT NULL DEFAULT true,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, email)   -- mesmo e-mail pode existir em tenants diferentes (ex: consultor multi-clínica)
);

-- CITEXT já habilitado no topo do arquivo (extensão precisa existir
-- antes desta tabela, que usa o tipo CITEXT na coluna email).


-- =====================================================================
-- TABELA: insurance_plans (Convênios)
--
-- DECISÃO #4 — normalized_key
-- A Etapa 2 do pipeline (normalização) precisa unificar variações de nome
-- do mesmo convênio vindas de arquivos diferentes (ex.: "Unimed Nacional",
-- "UNIMED NAC.", "unimed-nacional") em um único registro. Resolvemos isso
-- com uma coluna normalized_key (slug determinístico gerado pela app antes
-- do insert) + índice único por tenant. A tabela de "aliases" abaixo guarda
-- o histórico de variações vistas, útil para auditoria e para melhorar o
-- matching ao longo do tempo.
-- =====================================================================
CREATE TABLE core.insurance_plans (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    display_name     VARCHAR(255) NOT NULL,
    normalized_key   VARCHAR(255) NOT NULL,   -- ex: "unimed_nacional"
    ans_registry     VARCHAR(20),             -- registro ANS, se aplicável
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, normalized_key)
);

CREATE TABLE core.insurance_plan_aliases (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    insurance_plan_id UUID NOT NULL REFERENCES core.insurance_plans(id) ON DELETE CASCADE,
    raw_value        VARCHAR(255) NOT NULL,   -- string exatamente como veio no arquivo importado
    source_file      VARCHAR(255),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- =====================================================================
-- TABELA: contracts (Tabela de repasse / valores acordados por convênio)
-- Usada pela Etapa 3 (Regras de Contrato) para comparar "valor cobrado vs
-- valor acordado".
-- =====================================================================
CREATE TABLE core.contracts (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    insurance_plan_id  UUID NOT NULL REFERENCES core.insurance_plans(id) ON DELETE CASCADE,
    procedure_code     VARCHAR(20)  NOT NULL,   -- ex: código TUSS
    procedure_desc     VARCHAR(255),
    agreed_value       NUMERIC(12,2) NOT NULL CHECK (agreed_value >= 0),
    valid_from         DATE NOT NULL,
    valid_until        DATE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, insurance_plan_id, procedure_code, valid_from)
);


-- =====================================================================
-- TABELA: patients
-- =====================================================================
CREATE TABLE core.patients (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    full_name       VARCHAR(255) NOT NULL,
    cpf             VARCHAR(14),              -- nullable: nem todo paciente estrangeiro tem CPF
    birth_date      DATE,
    -- Rastreamento de origem de marketing (Etapa 3: Cálculo de ROI)
    acquisition_source   VARCHAR(50),         -- ex: 'meta_ads', 'google_ads', 'organico', 'indicacao'
    acquisition_campaign_id VARCHAR(100),     -- ID da campanha de origem, se aplicável
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, cpf)
);


-- =====================================================================
-- TABELA: appointments (Atendimentos)
-- =====================================================================
CREATE TABLE core.appointments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    patient_id      UUID NOT NULL REFERENCES core.patients(id) ON DELETE RESTRICT,
    insurance_plan_id UUID REFERENCES core.insurance_plans(id) ON DELETE SET NULL,
    scheduled_at    TIMESTAMPTZ NOT NULL,
    status          VARCHAR(30) NOT NULL DEFAULT 'scheduled'
                        CHECK (status IN ('scheduled','confirmed','completed','cancelled','no_show')),
    procedure_code  VARCHAR(20),
    cid_code        VARCHAR(10),   -- CID-10; ausência deste campo é um dos gatilhos de "alto risco de glosa"
    created_by      UUID REFERENCES core.users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- =====================================================================
-- TABELA: billing (Faturamento) — núcleo do RCM
--
-- DECISÃO #5 — Campos de auditoria de glosa direto na tabela principal
-- Em vez de uma tabela separada "denial_predictions", incorporamos
-- glosa_risk_level / glosa_reasons diretamente em billing. Isso porque toda
-- linha de faturamento tem no máximo UM estado de risco por vez (não é uma
-- relação 1:N) e manter no mesmo registro simplifica MUITO as queries da
-- Tela B (Painel Anti-Glosa) — sem JOIN adicional em um dashboard que precisa
-- ser rápido. Se no futuro precisarmos de histórico de reavaliações de risco,
-- extraímos um "billing_risk_history" append-only.
-- =====================================================================
CREATE TABLE core.billing (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    appointment_id      UUID NOT NULL REFERENCES core.appointments(id) ON DELETE RESTRICT,
    insurance_plan_id   UUID NOT NULL REFERENCES core.insurance_plans(id) ON DELETE RESTRICT,
    charged_value       NUMERIC(12,2) NOT NULL CHECK (charged_value >= 0),
    agreed_value_snapshot NUMERIC(12,2), -- copiado de contracts no momento do faturamento (histórico imutável)
    status              VARCHAR(30) NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','held_for_review','submitted','paid','denied','reversed')),
    -- Motor de IA / Regras (Etapa 3)
    denial_risk_level   VARCHAR(20) NOT NULL DEFAULT 'low'
                            CHECK (denial_risk_level IN ('low','medium','high')),
    denial_reasons      JSONB NOT NULL DEFAULT '[]'::jsonb,  -- ex: ["missing_cid","value_mismatch"]
    value_saved_by_correction NUMERIC(12,2) DEFAULT 0,  -- alimenta a Tela B ("valor salvo")
    submitted_at        TIMESTAMPTZ,
    paid_at              TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_billing_tenant_status ON core.billing (tenant_id, status);
CREATE INDEX idx_billing_denial_risk   ON core.billing (tenant_id, denial_risk_level) WHERE denial_risk_level <> 'low';


-- =====================================================================
-- TABELA: marketing_spend (para Tela C — Inteligência RevOps)
-- Guarda o gasto por campanha, vindo do webhook/ETL do Meta/Google Ads.
-- =====================================================================
CREATE TABLE core.marketing_spend (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    source          VARCHAR(30) NOT NULL CHECK (source IN ('meta_ads','google_ads')),
    campaign_id     VARCHAR(100) NOT NULL,
    campaign_name   VARCHAR(255),
    spend_date      DATE NOT NULL,
    amount_spent    NUMERIC(12,2) NOT NULL DEFAULT 0,
    impressions     BIGINT,
    clicks          BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, source, campaign_id, spend_date)
);


-- =====================================================================
-- TABELA: audit_log
-- DECISÃO #6 — Auditoria é obrigatória em HealthTech (rastreabilidade de
-- quem acessou/alterou dado de faturamento e paciente). Guardamos o mínimo
-- necessário (ator, ação, entidade, diff) sem virar um EAV genérico demais.
-- =====================================================================
CREATE TABLE core.audit_log (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    actor_user_id   UUID REFERENCES core.users(id),
    action          VARCHAR(50) NOT NULL,      -- ex: 'billing.update_status'
    entity_type     VARCHAR(50) NOT NULL,
    entity_id       UUID NOT NULL,
    diff            JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- =====================================================================
-- ROW-LEVEL SECURITY (RLS)
-- =====================================================================
-- DECISÃO #7 — Como o tenant "atual" é comunicado ao Postgres
-- A aplicação (FastAPI), logo após autenticar o JWT e resolver o tenant_id
-- do usuário, executa em CADA conexão/transação:
--
--     SET LOCAL app.current_tenant = '<uuid-do-tenant>';
--
-- "SET LOCAL" garante que o valor vale apenas para a transação corrente e
-- é limpo automaticamente no COMMIT/ROLLBACK — isso é crítico quando se usa
-- connection pooling (pgbouncer/asyncpg pool), porque impede que o tenant
-- de uma requisição "vaze" para a próxima requisição que reutilizar a mesma
-- conexão física. current_setting() lê essa variável de sessão dentro da
-- policy abaixo.
--
-- Usamos current_setting('app.current_tenant', true) com o segundo
-- argumento "true" (missing_ok) para que, se por algum motivo a variável
-- não for setada, a função retorne NULL em vez de lançar erro — e como
-- "tenant_id = NULL" nunca é verdadeiro, o resultado prático é ZERO linhas
-- visíveis. Ou seja: falha segura (fail-closed), nunca fail-open.
-- =====================================================================

-- Função helper: centraliza a leitura do tenant atual em um único lugar,
-- assim, se um dia mudarmos o mecanismo (ex: para JWT claims via
-- pgjwt), alteramos apenas esta função e não 8 policies espalhadas.
CREATE OR REPLACE FUNCTION core.current_tenant_id()
RETURNS UUID
LANGUAGE sql
STABLE
AS $$
    SELECT NULLIF(current_setting('app.current_tenant', true), '')::UUID;
$$;

-- Habilita RLS + FORCE em todas as tabelas com tenant_id.
-- "ENABLE ROW LEVEL SECURITY" ativa as policies para usuários comuns.
-- "FORCE ROW LEVEL SECURITY" é o que garante que NEM O DONO DA TABELA
-- (o usuário/role que a aplicação usa para rodar migrations, por exemplo)
-- fica isento das regras. Sem FORCE, um bug que conecte com o role owner
-- da tabela ignoraria o RLS silenciosamente — foi justamente esse tipo de
-- "escape hatch" que queremos eliminar, já que o requisito é isolamento
-- MATEMATICAMENTE garantido, não apenas "garantido enquanto o app se
-- comportar corretamente".
DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT unnest(ARRAY[
            'users','insurance_plans','insurance_plan_aliases','contracts',
            'patients','appointments','billing','marketing_spend','audit_log'
        ])
    LOOP
        EXECUTE format('ALTER TABLE core.%I ENABLE ROW LEVEL SECURITY;', t);
        EXECUTE format('ALTER TABLE core.%I FORCE ROW LEVEL SECURITY;', t);
    END LOOP;
END $$;

-- Policy padrão, replicada por tabela (Postgres não permite uma policy
-- "genérica" cross-table, então geramos uma por tabela via DO block).
--
-- USING       -> filtra quais linhas EXISTENTES são visíveis em SELECT/UPDATE/DELETE.
-- WITH CHECK  -> valida quais linhas podem ser CRIADAS/ATUALIZADAS (INSERT/UPDATE).
-- Aplicamos ambos com a mesma condição para impedir tanto "ler dado de
-- outro tenant" quanto "inserir/mover um dado para dentro de outro tenant".
DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT unnest(ARRAY[
            'users','insurance_plans','insurance_plan_aliases','contracts',
            'patients','appointments','billing','marketing_spend','audit_log'
        ])
    LOOP
        EXECUTE format($f$
            CREATE POLICY tenant_isolation_%1$I ON core.%1$I
            USING (tenant_id = core.current_tenant_id())
            WITH CHECK (tenant_id = core.current_tenant_id());
        $f$, t);
    END LOOP;
END $$;

-- Observação: a própria tabela "tenants" NÃO recebe RLS por tenant_id (ela
-- não tem essa coluna — ela É a entidade tenant). O acesso a "tenants" deve
-- ser restrito por outra via: normalmente só o role de sistema/admin da
-- plataforma consulta essa tabela diretamente; o usuário comum da clínica
-- nunca lista outros tenants pela API.


-- =====================================================================
-- ROLES DE BANCO (separado dos "roles de aplicação" da tabela users!)
-- DECISÃO #8 — Dois roles de conexão distintos
--   app_runtime : usado pelo backend FastAPI no dia a dia (CRUD sujeito a RLS)
--   app_migrator: usado apenas pelo Alembic para rodar DDL/migrations
-- Separar os dois evita que a mesma credencial que atende requisições HTTP
-- tenha privilégio de alterar schema — reduz a superfície de um ataque via
-- SQL injection residual ou credencial vazada.
-- =====================================================================
-- CREATE ROLE app_runtime LOGIN PASSWORD '<via secrets manager>';
-- GRANT USAGE ON SCHEMA core TO app_runtime;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core TO app_runtime;
-- (Comentado propositalmente: credenciais e criação de role devem ser
-- gerenciadas via Terraform/IaC + Secrets Manager, nunca hardcoded em SQL
-- versionado no repositório.)
