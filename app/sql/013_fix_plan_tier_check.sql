-- app/sql/013_fix_plan_tier_check.sql
--
-- BUG CRÍTICO DE PRODUÇÃO — CHECK constraint de core.tenants.plan_tier
-- nunca foi atualizada quando o valor de tier passou de "pro" para
-- "professional" em todo o resto do sistema (AVAILABLE_PLAN_TIERS em
-- app/schemas/tenant.py, o formulário de cadastro no frontend). Achado
-- via teste com dado sintético realista (scripts/seed_demo_data.py):
-- todo POST /api/v1/auth/register com plan_tier="professional" — que é
-- o plano PADRÃO pré-selecionado e "Mais escolhido" na tela de cadastro
-- — falhava com IntegrityError na inserção do tenant, devolvido ao
-- cliente como 409 "Já existe uma clínica cadastrada com este CNPJ"
-- (mensagem genérica de RegisterService, que trata QUALQUER IntegrityError
-- na criação do tenant como conflito de CNPJ — mascarando a causa real).
-- Na prática: cadastro público quebrado para quem não trocasse o plano
-- do padrão sugerido.
--
-- Mesmo padrão de 002_auth_resolver.sql/011_annual_revenue_goal.sql:
-- self-idempotente por construção (DROP IF EXISTS + ADD), sem entrar em
-- _POST_UPGRADE_MARKER_TABLE — seguro rodar em todo deploy.
ALTER TABLE core.tenants DROP CONSTRAINT IF EXISTS tenants_plan_tier_check;
ALTER TABLE core.tenants
    ADD CONSTRAINT tenants_plan_tier_check CHECK (plan_tier IN ('starter', 'professional', 'enterprise'));
