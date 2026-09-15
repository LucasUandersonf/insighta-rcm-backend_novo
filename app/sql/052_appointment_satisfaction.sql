-- app/sql/052_appointment_satisfaction.sql
--
-- "Mapa de Dados Insighta" — Domínio Pós-atendimento (Onda 2), pilar
-- Satisfação/NPS do paciente: hoje o produto não capturava NENHUM sinal
-- de satisfação pós-consulta — só dados administrativos/financeiros do
-- atendimento, nunca a percepção de quem foi atendido.
--
-- DECISÃO — link público de avaliação em vez de mensagem automática de
-- WhatsApp
-------------------------------------------------------------------------
-- O canal óbvio para pedir uma nota pós-consulta é WhatsApp, mas a API
-- oficial da Meta exige um TEMPLATE DE MENSAGEM pré-aprovado para iniciar
-- conversa fora da janela de 24h — algo que precisa ser submetido e
-- aprovado pela própria Meta, fora do alcance de uma migration de banco.
-- Em vez de bloquear a captura inteira nisso, o "gatilho" vira um link
-- público de uso único (mesmo padrão de core.password_reset_tokens: sem
-- RLS, só o hash do token é gravado) que a recepção GERA no sistema e
-- ENVIA manualmente pelo canal que já usa com o paciente (WhatsApp Web,
-- SMS) — sem exigir integração/aprovação nenhuma para já começar a
-- capturar o dado.
--
-- DECISÃO — score 1-5 (não NPS 0-10)
-------------------------------------------------------------------------
-- Uma escala de 5 estrelas é o padrão de mercado para "avalie seu
-- atendimento" (Google, iFood, Uber) — mais familiar para o paciente
-- responder num link sem contexto adicional do que uma escala 0-10 que
-- pressupõe a pergunta "o quanto você recomendaria".
ALTER TABLE core.appointments ADD COLUMN IF NOT EXISTS visit_satisfaction_score SMALLINT;

DO $$ BEGIN
  ALTER TABLE core.appointments ADD CONSTRAINT appointments_visit_satisfaction_score_check
    CHECK (visit_satisfaction_score IS NULL OR (visit_satisfaction_score >= 1 AND visit_satisfaction_score <= 5));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

COMMENT ON COLUMN core.appointments.visit_satisfaction_score IS
  '"Mapa de Dados Insighta" — nota de 1 a 5 dada pelo paciente via link público de avaliação (ver core.appointment_satisfaction_tokens). NULL = nunca avaliado.';

-- TABELA core.appointment_satisfaction_tokens — SEM RLS, mesmo raciocínio
-- de core.password_reset_tokens (ver 012_password_reset.sql): o paciente
-- acessa o link sem estar autenticado, sem contexto de tenant nenhum —
-- resolver "de qual tenant é esse token" é justamente o trabalho desta
-- tabela. Só o hash SHA-256 do token é gravado; o valor em texto puro só
-- existe no link copiado/enviado pela recepção.
CREATE TABLE IF NOT EXISTS core.appointment_satisfaction_tokens (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    appointment_id UUID NOT NULL REFERENCES core.appointments(id) ON DELETE CASCADE,
    tenant_id      UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    token_hash     VARCHAR(64) NOT NULL UNIQUE,
    expires_at     TIMESTAMPTZ NOT NULL,
    used_at        TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_appointment_satisfaction_tokens_appointment_id
  ON core.appointment_satisfaction_tokens (appointment_id);

COMMENT ON TABLE core.appointment_satisfaction_tokens IS
  'Tokens de uso único para o link público de avaliação pós-atendimento. '
  'Sem RLS de propósito — ver cabeçalho deste arquivo. Só o hash do token '
  'é gravado; o valor em texto puro só existe no link enviado ao paciente.';
