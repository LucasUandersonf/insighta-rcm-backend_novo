-- =====================================================================
-- ARQUIVO: 037_billing_glosa_hardening.sql
-- Correções da Auditoria de Templates e Insights (Analista de BI,
-- Engenheiro de Dados, Cientista de Dados, Analista de Dados, Engenheiro
-- de Analytics) sobre a chave de conciliação alternativa de Glosa por
-- carteirinha, introduzida em 036_billing_appointment_extended_fields.sql.
--
-- DECISÃO — Achado 2 (alto): índice para member_card_number, mesmo
-- padrão de ix_billing_guia_id
-- -------------------------------------------------------------------
-- guia_id ganhou índice parcial (WHERE guia_id IS NOT NULL) quando
-- entrou como chave de busca de BillingRepository.list_by_guia (ver
-- 015_billing_guia.sql). member_card_number é usado exatamente do mesmo
-- jeito por BillingRepository.list_by_member_card/
-- list_by_member_card_and_procedure_code
-- (NormalizationService.normalize_glosa_row) e ficou sem esse índice na
-- migration que o introduziu — sem efeito perceptível com poucas linhas,
-- mas um sequential scan garantido conforme core.billing cresce.
-- =====================================================================

CREATE INDEX IF NOT EXISTS ix_billing_member_card
    ON core.billing (insurance_plan_id, member_card_number)
    WHERE member_card_number IS NOT NULL;
