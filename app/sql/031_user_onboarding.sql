-- app/sql/031_user_onboarding.sql
--
-- Tour de boas-vindas guiado — ver Laudo de Vistoria Técnica (parecer
-- PM/PO): até aqui não existia nenhum onboarding dentro do produto, um
-- cliente novo abria o sistema pela primeira vez e precisava descobrir
-- sozinho onde cada coisa está.
--
-- DECISÃO — onboarding_completed_at em core.users (por PESSOA), não em
-- core.tenants (por clínica)
-------------------------------------------------------------------------
-- Cada COLABORADOR vê o tour na primeira vez que ENTRA, não só o
-- primeiro usuário da clínica — um financeiro contratado 6 meses depois
-- do owner que criou a conta também nunca viu a ferramenta antes e se
-- beneficia do mesmo tour. Guardar por tenant faria o segundo, terceiro
-- etc. usuário da mesma clínica nunca ver o tour, mesmo entrando pela
-- primeira vez.
--
-- DECISÃO — TIMESTAMPTZ (quando concluiu), não BOOLEAN (concluiu ou não)
-------------------------------------------------------------------------
-- Mesmo raciocínio de patients.anonymized_at (022_patient_lgpd_erasure.sql)
-- e tenants.annual_revenue_goal (011_annual_revenue_goal.sql): guardar
-- QUANDO, não só SE, não custa nada a mais e já responde de graça uma
-- pergunta futura de produto ("quantos dos usuários que entraram nesta
-- semana já passaram pelo tour?") sem precisar de uma coluna nova depois.
-- NULL = ainda não viu/concluiu; qualquer timestamp = concluiu (ou pulou
-- — ver DECISÃO em app/services/user_service.py: "pular" e "concluir"
-- gravam o mesmo jeito, a intenção do produto é só "não mostrar de novo
-- sem eu pedir", não medir quem prestou atenção em cada passo).
--
-- Mesmo padrão de idempotência de 011_annual_revenue_goal.sql: ADD
-- COLUMN IF NOT EXISTS é idempotente por natureza, então este arquivo
-- entra em _POST_UPGRADE_SQL_FILES SEM marcador em
-- _POST_UPGRADE_MARKER_TABLE (ver app/scripts/bootstrap_db.py).
ALTER TABLE core.users
    ADD COLUMN IF NOT EXISTS onboarding_completed_at TIMESTAMPTZ;

COMMENT ON COLUMN core.users.onboarding_completed_at IS
  'Quando este usuário concluiu (ou pulou) o tour de boas-vindas guiado. '
  'NULL = ainda não viu — o frontend mostra o tour na próxima sessão.';
