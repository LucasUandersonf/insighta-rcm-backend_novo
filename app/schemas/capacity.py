from uuid import UUID

from pydantic import BaseModel


class UtilizationResponse(BaseModel):
    professional_id: UUID
    professional_name: str
    available_minutes: int
    booked_minutes: int
    utilization_rate: float
    no_show_rate: float
    total_appointments: int
    status_breakdown: dict[str, int]
