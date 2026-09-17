-- app/sql/058_patient_full_identity.sql
--
-- Escopo completo de "pessoa física" no cadastro de paciente — pedido
-- direto do usuário: "todo sistema tem dados de pessoa física com nome,
-- telefone, data de nascimento, endereço, email, CPF, sexo". Até esta
-- migration, `cpf`/`birth_date` existiam na tabela mas SEM nenhum jeito
-- de chegar lá pela ingestão em massa (ver DECISÃO completa em
-- app/worker/schemas.py::RawAppointmentRow/RawBillingRow e
-- app/services/normalization_service.py) — a base real de paciente
-- (que entra 100% por importação, não por CRUD manual, ver
-- app/README.md "reposicionamento de produto") nunca tinha telefone,
-- e-mail, sexo ou endereço completo, mesmo esses campos sendo
-- rotineiros em qualquer ERP de clínica de origem.
--
-- DECISÃO — sexo como vocabulário FECHADO (M/F), não texto livre
-------------------------------------------------------------------------
-- Mesmo critério de tipo_paciente/guia_tipo: um vocabulário fechado e
-- pequeno (sexo biológico, relevante para triagem/exame, não identidade
-- de gênero autodeclarada) evita a mesma armadilha de "Masculino"/
-- "masculino"/"M"/"Homem" nunca se agregarem juntos num relatório. Sem
-- CHECK aqui (a validação de verdade mora no Pydantic, ver
-- RawAppointmentRow.normalize_sex) — mesmo padrão de tipo_paciente, que
-- também não tem CHECK na coluna, só no schema de entrada.
--
-- DECISÃO — endereço em 4 colunas (logradouro livre, cidade, UF, CEP),
-- não uma tabela de endereço à parte
-------------------------------------------------------------------------
-- Uma clínica não tem múltiplos endereços por paciente (isso seria
-- "Local de Atendimento" da clínica, já modelado em core.locais — outra
-- entidade). Cabe 1:1 na própria linha, mesmo raciocínio de zip_code já
-- existente na Onda 1 do Mapa de Dados. `address_street` é texto livre
-- (logradouro + número + complemento juntos) de propósito: a maioria
-- dos exports de ERP de origem já vem assim numa coluna só, forçar a
-- clínica a quebrar em 3 colunas na hora de mapear o template
-- adicionaria atrito sem ganho de análise (a demografia usa cidade/UF/
-- CEP agregados, nunca o logradouro exato).
--
-- Auto-idempotente (ADD COLUMN IF NOT EXISTS) — roda em todo deploy via
-- bootstrap_db.py, mesmo padrão do resto do arquivo core.patients.
ALTER TABLE core.patients ADD COLUMN IF NOT EXISTS phone VARCHAR(20);
ALTER TABLE core.patients ADD COLUMN IF NOT EXISTS email VARCHAR(255);
ALTER TABLE core.patients ADD COLUMN IF NOT EXISTS sex VARCHAR(1);
ALTER TABLE core.patients ADD COLUMN IF NOT EXISTS address_street VARCHAR(255);
ALTER TABLE core.patients ADD COLUMN IF NOT EXISTS address_city VARCHAR(100);
ALTER TABLE core.patients ADD COLUMN IF NOT EXISTS address_state VARCHAR(2);

DO $$ BEGIN
  ALTER TABLE core.patients ADD CONSTRAINT patients_sex_check
    CHECK (sex IS NULL OR sex IN ('M', 'F'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

COMMENT ON COLUMN core.patients.phone IS
  'Telefone de contato (só dígitos, com DDD) — capturado pela ingestão de Agenda/Faturamento ou cadastro manual. NULL = não informado.';
COMMENT ON COLUMN core.patients.email IS
  'E-mail de contato. NULL = não informado. Validação de formato mora no Pydantic (RawAppointmentRow/PatientCreateRequest), não em CHECK de banco.';
COMMENT ON COLUMN core.patients.sex IS
  'Sexo biológico, vocabulário FECHADO (M/F) — ver DECISÃO acima. NULL = não informado.';
COMMENT ON COLUMN core.patients.address_street IS
  'Logradouro + número + complemento em texto livre — ver DECISÃO acima sobre não quebrar em mais colunas.';
COMMENT ON COLUMN core.patients.address_city IS
  'Cidade do paciente. NULL = não informado.';
COMMENT ON COLUMN core.patients.address_state IS
  'UF (2 letras maiúsculas) do paciente. NULL = não informado.';
