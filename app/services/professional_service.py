import uuid

from app.models.professional import Professional
from app.models.professional_availability import ProfessionalAvailability
from app.repositories.professional_availability_repository import ProfessionalAvailabilityRepository
from app.repositories.professional_repository import ProfessionalRepository
from app.schemas.professional import ProfessionalCreateRequest, ProfessionalResponse

"""
DECISÃO — sem relationship() do SQLAlchemy entre Professional e
ProfessionalAvailability
-------------------------------------------------------------------------
Poderíamos declarar `availability: Mapped[list[ProfessionalAvailability]]`
como relationship() no model e deixar o ORM carregar automaticamente.
Evitamos isso de propósito: lazy loading assíncrono no SQLAlchemy exige
`selectinload`/`joinedload` explícito em toda query que toca o objeto,
sob risco de `MissingGreenlet` se algo acessar o atributo fora do
contexto async certo — uma pegadinha real e recorrente em projetos
FastAPI+SQLAlchemy async. Preferimos buscar a disponibilidade
explicitamente (`availability_repo.list_by_professional`) e atribuir
como atributo comum antes de serializar para o schema Pydantic — mais
verboso, mas sem armadilha de carregamento implícito.
"""


class ProfessionalService:
    def __init__(self, professional_repo: ProfessionalRepository, availability_repo: ProfessionalAvailabilityRepository):
        self.professional_repo = professional_repo
        self.availability_repo = availability_repo

    async def create_professional(self, tenant_id: str, data: ProfessionalCreateRequest) -> ProfessionalResponse:
        professional = await self.professional_repo.add(
            Professional(
                id=uuid.uuid4(),
                tenant_id=uuid.UUID(tenant_id),
                full_name=data.full_name,
                professional_registry=data.professional_registry,
                specialty=data.specialty,
            )
        )
        for block in data.availability:
            await self.availability_repo.add(
                ProfessionalAvailability(
                    tenant_id=uuid.UUID(tenant_id),
                    professional_id=professional.id,
                    weekday=block.weekday,
                    start_time=block.start_time,
                    end_time=block.end_time,
                )
            )
        # Recarrega a grade para devolver na resposta já com os blocos criados.
        professional.availability = await self.availability_repo.list_by_professional(professional.id)
        return ProfessionalResponse.model_validate(professional)

    async def list_professionals(self) -> list[ProfessionalResponse]:
        items = await self.professional_repo.list_active()
        # Uma query batelada em vez de N queries dentro do loop (ver
        # DECISÃO em ProfessionalAvailabilityRepository.list_by_professionals).
        availability_by_professional = await self.availability_repo.list_by_professionals([p.id for p in items])
        results = []
        for professional in items:
            professional.availability = availability_by_professional.get(professional.id, [])
            results.append(ProfessionalResponse.model_validate(professional))
        return results
