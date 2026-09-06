"""
app/services/platform_alert_service.py — alertas proativos de Customer
Success. Ver DECISÃO completa em app/sql/027_platform_risk_alerts.sql.

DECISÃO — alerta só na TRANSIÇÃO para "risco", com lembrete periódico
-------------------------------------------------------------------------
Reenviar o mesmo aviso a cada execução do job (ex: a cada 1h) inundaria
a caixa de entrada da equipe e ensinaria todo mundo a ignorar o alerta —
o mesmo problema de "alarme que sempre toca" que qualquer sistema de
monitoramento de verdade evita. Por isso: alerta NOVO quando a clínica
entra em risco agora e não estava antes (nenhuma linha em
core.platform_risk_alerts); LEMBRETE só depois de
`_RE_ALERT_INTERVAL_DAYS` sem novo aviso, caso ela continue em risco;
silêncio nos demais casos. Quando a clínica sai do risco, o episódio é
fechado (linha apagada) — uma entrada FUTURA em risco conta como alerta
novo, não um reenvio do mesmo episódio.

DECISÃO — e-mail, não WhatsApp, para este alerta
-------------------------------------------------------------------------
O backlog original citava "e-mail/WhatsApp" como alternativas. WhatsApp
(WhatsAppClient) exige um template pré-aprovado pela Meta E um número de
telefone individual por destinatário — criar um template só para um
aviso interno da própria equipe é desproporcional ao problema. E-mail
(EmailClient, já usado por SUPPORT_EMAIL) resolve o mesmo objetivo
("a equipe fica sabendo sem precisar checar a tela") com a
infraestrutura que já existe. Nada impede adicionar WhatsApp depois se
a equipe achar e-mail insuficiente.
"""
import logging
from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.repositories.platform_risk_alert_repository import PlatformRiskAlertRepository
from app.schemas.platform import PlatformAlertRunResponse, TenantUsageSummary
from app.services.email_client import EmailClient
from app.services.platform_reporting_service import PlatformReportingService

logger = logging.getLogger("platform_alert_service")
settings = get_settings()

# Depois de quantos dias sem um novo aviso um episódio de risco AINDA
# ABERTO merece um lembrete — evita o extremo oposto de "avisou uma vez
# há 3 meses e nunca mais", já que a clínica pode ter piorado desde então.
_RE_ALERT_INTERVAL_DAYS = 7


class PlatformAlertService:
    def __init__(
        self,
        reporting_service: PlatformReportingService,
        alert_repo: PlatformRiskAlertRepository,
        email_client: EmailClient | None = None,
    ):
        self.reporting_service = reporting_service
        self.alert_repo = alert_repo
        # Injetável para teste, mesmo padrão de SupportRequestService.
        self.email_client = email_client or EmailClient()

    async def check_and_send_risk_alerts(self, *, now: datetime | None = None) -> PlatformAlertRunResponse:
        now = now or datetime.now(timezone.utc)
        summaries = await self.reporting_service.list_tenant_usage()

        new_alerts: list[str] = []
        reminders_sent: list[str] = []
        recovered: list[str] = []

        for summary in summaries:
            existing = await self.alert_repo.get(summary.tenant_id)

            if summary.engagement_status == "risco":
                if existing is None:
                    await self.alert_repo.open_episode(summary.tenant_id, now=now)
                    await self._send_email(summary, is_reminder=False)
                    new_alerts.append(summary.trade_name)
                elif now - existing.last_alert_sent_at >= timedelta(days=_RE_ALERT_INTERVAL_DAYS):
                    await self.alert_repo.touch(existing, now=now)
                    await self._send_email(summary, is_reminder=True)
                    reminders_sent.append(summary.trade_name)
                # else: já alertado recentemente, este episódio segue em
                # silêncio até o próximo lembrete ou recuperação.
            elif existing is not None:
                # Saiu do risco — fecha o episódio SEM enviar e-mail de
                # "recuperada": o objetivo deste job é fazer alguém agir,
                # não gerar mais uma notificação que ninguém precisa ler.
                await self.alert_repo.close_episode(existing)
                recovered.append(summary.trade_name)

        return PlatformAlertRunResponse(new_alerts=new_alerts, reminders_sent=reminders_sent, recovered=recovered)

    async def _send_email(self, summary: TenantUsageSummary, *, is_reminder: bool) -> None:
        if not settings.PLATFORM_ALERT_EMAIL:
            logger.error(
                "PLATFORM_ALERT_EMAIL não configurado — alerta de risco de %s NÃO enviado por e-mail "
                "(o episódio continua registrado em core.platform_risk_alerts).",
                summary.trade_name,
            )
            return

        subject = (
            f"[Customer Success] {summary.trade_name} segue em risco de cancelamento"
            if is_reminder
            else f"[Customer Success] {summary.trade_name} entrou em risco de cancelamento"
        )
        last_activity = "nunca" if summary.last_activity_at is None else summary.last_activity_at.strftime("%d/%m/%Y")
        text_body = (
            f"Clínica: {summary.trade_name} (plano {summary.plan_tier})\n"
            f"Cliente desde: {summary.tenant_created_at.strftime('%d/%m/%Y')}\n"
            f"Última atividade: {last_activity}\n"
            f"Eventos nos últimos 30 dias: {summary.events_last_30d}\n"
            f"Usuários ativos: {summary.active_users}\n\n"
            f"Veja o painel completo em /plataforma para decidir o próximo passo."
        )
        try:
            await self.email_client.send(to_email=settings.PLATFORM_ALERT_EMAIL, subject=subject, text_body=text_body)
        except Exception:
            # O episódio JÁ foi gravado (open_episode/touch acontece ANTES
            # desta chamada) — uma falha de e-mail não pode fazer o job
            # inteiro parecer que travou nem, pior, tentar de novo a cada
            # execução só porque o e-mail falhou uma vez (o estado já
            # registra que o aviso foi "tentado agora").
            logger.exception("Falha ao enviar e-mail de alerta de risco para tenant=%s", summary.tenant_id)
