-- app/sql/023_announcements_and_support.sql
--
-- Central de Notificações (sino de novidades/changelog) + Central de
-- Ajuda (jeito de tirar dúvida sem sair do sistema) — item do backlog de
-- maturidade de produto ("nenhum SaaS B2B sobrevive sem onboarding/
-- suporte contextualizado").
--
-- Três tabelas novas:
--   1) platform_announcements — o changelog em si.
--   2) announcement_reads     — quem já viu qual novidade.
--   3) support_requests       — perguntas que o cliente manda sem sair
--                                do sistema (POST /support-requests).

-- ---------------------------------------------------------------------
-- TABELA: platform_announcements
--
-- DECISÃO — a ÚNICA tabela de todo o schema `core` SEM tenant_id/RLS
-- -------------------------------------------------------------------
-- Toda outra tabela deste projeto tem isolamento por tenant_id + RLS —
-- aqui é uma exceção DELIBERADA, não um esquecimento: uma novidade da
-- plataforma ("agora dá para gerar o documento de recurso em PDF
-- automaticamente") é a MESMA para todo tenant, publicada pela equipe
-- que opera a plataforma (hoje via script — app/scripts/publish_announcement.py
-- — não existe um "super-admin" com sessão HTTP própria neste produto).
-- Colocar tenant_id aqui obrigaria duplicar a MESMA linha uma vez por
-- tenant só para satisfazer uma convenção que não se aplica a este
-- dado. `app_runtime` já tem GRANT SELECT/INSERT/UPDATE/DELETE em todas
-- as tabelas do schema (ver _ROLES_SQL em bootstrap_db.py) — sem
-- ENABLE ROW LEVEL SECURITY aqui, esse GRANT já basta para leitura
-- irrestrita por qualquer sessão autenticada, que é exatamente o
-- comportamento desejado.
CREATE TABLE core.platform_announcements (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title           VARCHAR(200) NOT NULL,
    body            TEXT NOT NULL,
    -- Toda linha nasce publicada (sem estado de rascunho) — não existe
    -- fluxo de "preparar e agendar" nesta primeira versão; quem escreve
    -- a linha (ver publish_announcement.py) já revisou o texto antes.
    published_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_platform_announcements_published_at ON core.platform_announcements (published_at DESC);

-- ---------------------------------------------------------------------
-- TABELA: announcement_reads — "quem já viu o quê", por usuário
--
-- Esta SIM é tenant-scoped/RLS normal: é estado de UM usuário
-- específico (dentro de um tenant específico), não conteúdo
-- compartilhado como platform_announcements acima.
CREATE TABLE core.announcement_reads (
    tenant_id       UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
    announcement_id UUID NOT NULL REFERENCES core.platform_announcements(id) ON DELETE CASCADE,
    read_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, announcement_id)
);

CREATE INDEX ix_announcement_reads_tenant ON core.announcement_reads (tenant_id);

-- ---------------------------------------------------------------------
-- TABELA: support_requests — Central de Ajuda, "enviar uma pergunta"
--
-- Sem integração de chat ao vivo (nenhuma conta de terceiro cotada
-- ainda) — o mecanismo aqui é o mesmo espírito de
-- app/services/email_client.py: sempre GRAVA a pergunta (nunca perde o
-- pedido do cliente), e OPCIONALMENTE também dispara um e-mail de aviso
-- para settings.SUPPORT_EMAIL quando configurado. status existe para o
-- cliente ver "sua pergunta anterior já foi respondida?" mesmo sem
-- nenhuma tela de atendimento construída ainda (atualização de status é
-- manual, direto no banco, até essa tela existir).
CREATE TABLE core.support_requests (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES core.users(id),
    subject         VARCHAR(200) NOT NULL,
    message         TEXT NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'aberto' CHECK (status IN ('aberto', 'respondido')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_support_requests_tenant ON core.support_requests (tenant_id, created_at DESC);

DO $$
DECLARE
    t TEXT;
BEGIN
    -- platform_announcements FICA DE FORA de propósito — ver DECISÃO acima.
    FOR t IN
        SELECT unnest(ARRAY['announcement_reads', 'support_requests'])
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
