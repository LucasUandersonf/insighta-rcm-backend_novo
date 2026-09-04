from datetime import date

from pydantic import BaseModel


class WeeklyReportRequest(BaseModel):
    # Opcional: por padrão usa a última semana fechada (segunda a domingo
    # anterior); permite pedir um período customizado sob demanda.
    period_start: date | None = None
    period_end: date | None = None


class WeeklyReportResponse(BaseModel):
    period_start: date
    period_end: date
    sent_via_whatsapp: bool
    # Detalhamento por destinatário — ver DECISÃO em
    # app/services/report_send_service.py (múltiplos destinatários por
    # tenant desde core.report_recipients). recipients_checked=0 é o
    # caminho feliz de "ninguém cadastrado ainda", não um erro.
    recipients_checked: int = 0
    sent: int = 0
    failed: int = 0
    detail: str
