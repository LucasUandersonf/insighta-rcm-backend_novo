"""
app/services/announcement_service.py — Central de Notificações (sino de
novidades/changelog). Ver DECISÃO completa em
app/sql/023_announcements_and_support.sql e AnnouncementRepository.
"""
import uuid

from fastapi import HTTPException, status

from app.repositories.announcement_repository import AnnouncementRepository
from app.schemas.announcement import AnnouncementListResponse, AnnouncementResponse


class AnnouncementService:
    def __init__(self, repo: AnnouncementRepository):
        self.repo = repo

    async def list_for_user(self, user_id: uuid.UUID) -> AnnouncementListResponse:
        items = await self.repo.list_for_user(user_id)
        unread_count = await self.repo.count_unread(user_id)
        return AnnouncementListResponse(
            items=[AnnouncementResponse.model_validate(i) for i in items], unread_count=unread_count
        )

    async def mark_read(self, tenant_id: str, user_id: uuid.UUID, announcement_id: uuid.UUID) -> None:
        if not await self.repo.exists(announcement_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Novidade não encontrada.")
        await self.repo.mark_read(tenant_id=uuid.UUID(tenant_id), user_id=user_id, announcement_id=announcement_id)
