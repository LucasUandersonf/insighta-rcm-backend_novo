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
    detail: str
