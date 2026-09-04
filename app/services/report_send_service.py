"""
app/services/report_send_service.py

Núcleo COMPARTILHADO de "gerar o relatório semanal (PDF) e mandar para os
destinatários certos de um tenant", extraído de
app/worker/weekly_report_job.py para ter DOIS chamadores sobre a MESMA
implementação — nenhuma lógica de montagem/envio de relatório duplicada
entre eles (mesmo padrão de app/services/ingestion_processing_service.py,
que existe pelo mesmo motivo para o pipeline de ingestão):

  1. app/worker/weekly_report_job.py — cron EXTERNO, processa TODOS os
     tenants ativos, sempre com o período "última semana fechada"
     (dado completo, nunca uma semana ainda em andamento).
  2. app/api/v1/endpoints/reports.py (`POST /reports/weekly/send`) —
     disparo SOB DEMANDA de UM tenant. Achado do usuário sobre lacuna do
     produto ("o relatório só sai na janela semanal, a menos que o
     gestor abra o dashboard sozinho"): aqui o período PADRÃO é o
     oposto — "desde segunda até HOJE", dado parcial de propósito,
     porque quem aciona este endpoint já sabe que quer o retrato mais
     fresco possível, não uma semana fechada.

BUG CORRIGIDO — o endpoint HTTP usava `Tenant.whatsapp_group_id`
-------------------------------------------------------------------------
Antes desta extração, `POST /reports/weekly/send` ainda lia
`tenant.whatsapp_group_id` (o destino ÚNICO de antes de
009_report_recipients.sql) — um campo que o cron (`weekly_report_job.py`)
já não usa há uma migration inteira, substituído por
`core.report_recipients` (N destinatários por tenant, cada um com seus
próprios tipos de relatório). Resultado prático: para qualquer tenant que
migrou para o cadastro de destinatários novo (que é a única forma
exposta hoje na tela de Setup — ver ReportRecipientRepository), o botão
de "enviar agora" respondia sempre "número não configurado", mesmo com
destinatários cadastrados corretamente. Esta extração faz os DOIS
caminhos usarem a MESMA fonte de verdade (ReportRecipientRepository),
então esse desalinhamento não pode mais acontecer de novo.

DECISÃO — a função recebe a sessão JÁ aberta e tenant-aware
-------------------------------------------------------------------------
Mesmo motivo documentado em ingestion_processing_service.py: quem decide
COMO abrir a sessão (o worker via get_db_with_tenant, o endpoint HTTP via
DbSession já injetada pelo FastAPI) é responsabilidade do CHAMADOR — esta
função nunca abre sessão nem decide tenant sozinha, o que a mantém segura
de reutilizar sem risco de vazar dado entre tenants.
"""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant
from app.repositories.capacity_repository import CapacityRepository
from app.repositories.professional_availability_repository import ProfessionalAvailabilityRepository
from app.repositories.professional_repository import ProfessionalRepository
from app.repositories.report_recipient_repository import ReportRecipientRepository
from app.repositories.reporting_repository import ReportingRepository
from app.services.report_data_service import ReportDataService
from app.services.report_pdf_builder import build_weekly_report_pdf
from app.services.whatsapp_client import WhatsAppClient, WhatsAppClientError

# Tipo de relatório usado para filtrar core.report_recipients (ver
# DECISÃO em app/sql/009_report_recipients.sql) — único tipo que este
# serviço produz hoje. Um destinatário com `report_types` vazio recebe
# este (e qualquer outro) tipo; um destinatário com lista não-vazia só
# recebe este relatório se "weekly_summary" estiver nela.
REPORT_TYPE = "weekly_summary"


@dataclass
class ReportSendResult:
    recipients_checked: int  # destinatários com WhatsApp cadastrado, elegíveis para este tipo de relatório
    sent: int
    failed: int


async def send_report_to_recipients(
    db: AsyncSession,
    tenant: Tenant,
    period_start: date,
    period_end: date,
    client_factory: Callable[[], WhatsAppClient],
) -> ReportSendResult:
    """
    Busca os destinatários elegíveis, monta o PDF (uma vez) e envia via
    WhatsApp a cada um. `recipients_checked == 0` é o caminho FELIZ de
    "tenant sem destinatário de relatório semanal cadastrado ainda" — não
    uma exceção, quem chama decide o que fazer com isso (logar e pular no
    cron, devolver uma mensagem clara no endpoint HTTP).

    DECISÃO — `client_factory`, não um `WhatsAppClient` já pronto
    -------------------------------------------------------------------
    `WhatsAppClient()` levanta `WhatsAppClientError` cedo se as
    credenciais da plataforma não estão configuradas (ver
    app/services/whatsapp_client.py). Se o CHAMADOR construísse o client
    antes de saber se há destinatário, "sem destinatário cadastrado" e
    "credenciais da plataforma ausentes" virariam o MESMO erro sempre que
    as duas coisas fossem verdade ao mesmo tempo — escondendo a causa
    mais acionável (cadastre um destinatário) atrás de uma menos
    acionável (fale com o time de infra). Só construímos o client DEPOIS
    de confirmar que existe pelo menos 1 destinatário elegível.

    Fan-out: um destinatário com número inválido/expirado na Meta não
    trava o envio para os demais do MESMO tenant — cada falha é isolada e
    contada em `failed`, nunca propagada como exceção. `WhatsAppClientError`
    da CONSTRUÇÃO do client (credenciais ausentes) continua propagando —
    isso é um problema de configuração da plataforma, não de um
    destinatário específico, então quem chama decide como reportar.
    """
    recipients = await ReportRecipientRepository(db).list_for_report_type(tenant.id, REPORT_TYPE)
    whatsapp_recipients = [r for r in recipients if r.phone_whatsapp]
    if not whatsapp_recipients:
        return ReportSendResult(recipients_checked=0, sent=0, failed=0)

    client = client_factory()

    data_service = ReportDataService(
        ReportingRepository(db),
        ProfessionalRepository(db),
        ProfessionalAvailabilityRepository(db),
        CapacityRepository(db),
    )
    report_data = await data_service.build_weekly_report(tenant.trade_name, period_start, period_end)
    pdf_bytes = build_weekly_report_pdf(report_data)
    filename = f"relatorio_semanal_{period_start.isoformat()}_a_{period_end.isoformat()}.pdf"

    sent, failed = 0, 0
    for recipient in whatsapp_recipients:
        try:
            await client.send_weekly_report(to_phone_number=recipient.phone_whatsapp, pdf_bytes=pdf_bytes, filename=filename)
            sent += 1
        except WhatsAppClientError:
            failed += 1

    return ReportSendResult(recipients_checked=len(whatsapp_recipients), sent=sent, failed=failed)
