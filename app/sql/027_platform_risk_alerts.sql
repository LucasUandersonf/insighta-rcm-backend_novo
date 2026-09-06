-- app/sql/027_platform_risk_alerts.sql
--
-- Alertas proativos de Customer Success: sem esta tabela, o painel de
-- app/sql/026_platform_customer_success.sql só ajuda quem lembra de
-- abrir a tela — este arquivo dá o estado necessário para AVISAR a
-- equipe Insighta assim que uma clínica ENTRA em "risco", em vez de
-- depender de alguém checar manualmente.
--
-- DECISÃO — mesma exceção de core.platform_announcements: SEM tenant_id
-- na linha em si, SEM RLS
-------------------------------------------------------------------------
-- Esta tabela não é dado de UMA clínica — é bookkeeping da PRÓPRIA
-- Insighta sobre o estado de alerta de todas elas. `tenant_id` aqui é
-- só uma referência (qual clínica está em episódio de risco), não um
-- dado que precisa ser isolado por RLS: o único caminho de código que
-- lê/escreve esta tabela é app/services/platform_alert_service.py,
-- acessado exclusivamente por app/api/platform_admin_auth.py — nunca
-- por uma sessão tenant-aware. Colocar RLS aqui exigiria rodar esta
-- escrita com `app.current_tenant` setado (um por clínica verificada),
-- quando na verdade o job PRECISA varrer TODAS de uma vez.
--
-- DECISÃO — tenant_id como CHAVE PRIMÁRIA (não um `id` próprio)
-------------------------------------------------------------------------
-- Existe no máximo UM episódio de risco "em aberto" por clínica a
-- qualquer momento (a linha É o episódio) — se a clínica sai do risco,
-- a linha é apagada (ver DECISÃO em PlatformAlertService); se ela
-- entra em risco de novo depois, nasce uma linha nova, um episódio
-- novo. Não há necessidade de histórico de episódios passados nesta
-- v1 — só "está em alerta agora ou não".
CREATE TABLE core.platform_risk_alerts (
    tenant_id           UUID PRIMARY KEY REFERENCES core.tenants(id) ON DELETE CASCADE,
    first_detected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_alert_sent_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE core.platform_risk_alerts IS
  'Episódio de risco EM ABERTO por clínica, para o painel interno de '
  'Customer Success (026_platform_customer_success.sql) não reenviar o '
  'mesmo alerta a cada execução do job. Linha apagada quando a clínica '
  'sai do status "risco" — ver PlatformAlertService.';
