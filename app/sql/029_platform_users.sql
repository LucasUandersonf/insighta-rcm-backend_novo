-- app/sql/029_platform_users.sql
--
-- Login individual da equipe da plataforma — substitui a senha única
-- compartilhada de app/api/platform_admin_auth.py (era uma escolha
-- deliberada de escopo da v1, ver comentário removido daquele arquivo).
-- Evolução natural agora que existe mais de uma ação real acontecendo
-- no painel (ver alerts/run) e faz sentido saber QUEM fez o quê.
--
-- DECISÃO — mesma exceção sem tenant_id/RLS de platform_announcements/
-- platform_risk_alerts
-------------------------------------------------------------------------
-- Login da equipe Insighta não é dado de clínica nenhuma — é bookkeeping
-- da própria plataforma, acessado exclusivamente pelo fluxo em
-- app/api/platform_admin_auth.py, nunca por uma sessão tenant-aware.
-- `app_runtime` já tem GRANT irrestrito em todas as tabelas do schema
-- (ver _ROLES_SQL em bootstrap_db.py); sem RLS aqui, esse GRANT já basta.
--
-- DECISÃO — sem RBAC próprio nesta v1 (todo platform_user pode tudo)
-------------------------------------------------------------------------
-- A equipe que usa este painel hoje é pequena e todos precisam do mesmo
-- acesso (ver uso agregado, disparar checagem de alerta). Introduzir
-- papéis distintos agora seria complexidade sem necessidade real —
-- evolução natural se o time crescer e surgir a necessidade de
-- diferenciar quem pode o quê.
CREATE TABLE core.platform_users (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email             CITEXT NOT NULL UNIQUE,
    hashed_password   VARCHAR(255) NOT NULL,
    full_name         VARCHAR(255) NOT NULL,
    is_active         BOOLEAN NOT NULL DEFAULT true,
    last_login_at     TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Histórico de ações do painel — hoje só duas: "login" e "alerts_run"
-- (POST /platform/alerts/run disparado manualmente). Deliberadamente
-- simples (sem `diff`, ao contrário de core.audit_log de clínica): as
-- ações que existem aqui não têm "antes/depois" de estado, só "isto
-- aconteceu, por esta pessoa, nesta hora".
CREATE TABLE core.platform_audit_log (
    id                BIGSERIAL PRIMARY KEY,
    platform_user_id  UUID NOT NULL REFERENCES core.platform_users(id) ON DELETE CASCADE,
    action            VARCHAR(50) NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_platform_audit_log_created_at ON core.platform_audit_log (created_at DESC);
