-- =====================================================================
-- ARQUIVO: 005_performance_indexes.sql
-- Índices em tenant_id para tabelas de alto tráfego que só tinham a
-- chave primária.
--
-- POR QUE ISSO IMPORTA COM RLS
-- ---------------------------------------------------------------------
-- Toda política de RLS deste projeto filtra por `tenant_id = current_tenant_id()`
-- (ver core.current_tenant_id() em 001_init_schema.sql). Isso significa
-- que TODA consulta a estas tabelas, mesmo um "SELECT * FROM patients"
-- sem nenhum WHERE explícito no código da aplicação, na prática executa
-- com um filtro por tenant_id imposto pelo Postgres. Sem índice nessa
-- coluna, o plano de execução faz varredura sequencial da tabela
-- INTEIRA (de TODOS os tenants) para depois descartar as linhas que não
-- são do tenant atual — um custo que cresce com o volume total de dado
-- na plataforma, não com o volume do tenant que está consultando. Com
-- poucos tenants e pouco dado isso não aparece nos testes; com 100
-- clínicas e anos de histórico acumulado, é o tipo de problema que
-- primeiro aparece como lentidão intermitente e difícil de reproduzir.
--
-- Tabelas como core.billing, core.appointments e core.professional_availability
-- já tinham índice composto com tenant_id como coluna líder (ver 001 e
-- 004) — esta migration cobre as que ficaram de fora até agora.
-- =====================================================================

SET search_path TO core, public;

CREATE INDEX IF NOT EXISTS idx_patients_tenant ON core.patients (tenant_id);
CREATE INDEX IF NOT EXISTS idx_contracts_tenant ON core.contracts (tenant_id);
CREATE INDEX IF NOT EXISTS idx_insurance_plans_tenant ON core.insurance_plans (tenant_id);
CREATE INDEX IF NOT EXISTS idx_professionals_tenant ON core.professionals (tenant_id);
CREATE INDEX IF NOT EXISTS idx_users_tenant ON core.users (tenant_id);

-- appointments já tinha um índice (tenant_id, professional_id, scheduled_at)
-- criado em 004 para o módulo de Capacidade — cobre também as queries do
-- motor de risco de falta (que buscam por patient_id, não professional_id).
-- Este índice complementar cobre a busca por paciente:
CREATE INDEX IF NOT EXISTS idx_appointments_patient ON core.appointments (tenant_id, patient_id, scheduled_at);
