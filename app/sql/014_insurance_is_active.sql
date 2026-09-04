-- app/sql/014_insurance_is_active.sql
--
-- Adiciona is_active a core.insurance_companies e core.insurance_plans —
-- mesmo padrão de "desligar sem apagar" já usado em professionals/users
-- (achado do usuário testando o cadastro: Convênio e Plano não tinham
-- NENHUMA forma de sair dos seletores, nem de exclusão nem de
-- desativação, mesmo cadastrados errado/duplicado por engano). Exclusão
-- de verdade não é opção aqui: Contract, Appointment, Billing e
-- insurance_plan_aliases referenciam insurance_plan_id — apagar
-- quebraria essas FKs (ou exigiria cascata destruindo histórico
-- financeiro real). `is_active=false` esconde o registro dos seletores
-- de cadastro NOVO (mesmo raciocínio de ProfessionalRepository.list_active
-- vs. list_all) sem tocar em nada que já exista.
--
-- Mesmo padrão de 002_auth_resolver.sql/013_fix_plan_tier_check.sql:
-- self-idempotente por construção (ADD COLUMN IF NOT EXISTS) — roda em
-- todo deploy via bootstrap_db.py, sem entrar em
-- _POST_UPGRADE_MARKER_TABLE.
ALTER TABLE core.insurance_companies ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE core.insurance_plans ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true;
