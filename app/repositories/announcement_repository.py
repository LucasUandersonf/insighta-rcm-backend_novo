"""
app/repositories/announcement_repository.py

DECISÃO — consultas SQL diretas (`text()`), não ORM `select()`
-------------------------------------------------------------------------
`Announcement` (core.platform_announcements) não tem `tenant_id` — é a
única tabela do schema sem RLS (ver DECISÃO em
app/sql/023_announcements_and_support.sql). O LEFT JOIN com
`announcement_reads` (que tem tenant_id/RLS normal) para calcular
`is_read` por usuário é mais direto em SQL puro do que compor duas
consultas ORM separadas e cruzar em Python.
"""
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.announcement import Announcement
from app.models.announcement_read import AnnouncementRead

# BUG CORRIGIDO — o import acima de `Announcement` não é decorativo:
# sem ele, nada no grafo de imports da aplicação carregava esse model
# (as consultas deste arquivo usam SQL puro, não `select(Announcement)`),
# e sem isso o SQLAlchemy nunca registrava `core.platform_announcements`
# no Base.metadata — `AnnouncementRead.announcement_id`, que referencia
# essa tabela por FK, quebrava com `NoReferencedTableError` na primeira
# vez que uma linha de `announcement_reads` era gravada (só descoberto
# testando de verdade contra Postgres, não por leitura de código).


class AnnouncementRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_for_user(self, user_id: uuid.UUID, *, limit: int = 30) -> list[dict]:
        """Mais recente primeiro, com `is_read` calculado para o usuário
        atual — dois usuários do MESMO tenant (ou até o mesmo usuário em
        momentos diferentes) veem `is_read` diferente, porque leitura é
        por pessoa, não por tenant."""
        stmt = text(
            """
            SELECT a.id, a.title, a.body, a.published_at, (ar.user_id IS NOT NULL) AS is_read
            FROM core.platform_announcements a
            LEFT JOIN core.announcement_reads ar ON ar.announcement_id = a.id AND ar.user_id = :user_id
            ORDER BY a.published_at DESC
            LIMIT :limit
            """
        )
        result = await self.session.execute(stmt, {"user_id": user_id, "limit": limit})
        return [
            {"id": row.id, "title": row.title, "body": row.body, "published_at": row.published_at, "is_read": row.is_read}
            for row in result.all()
        ]

    async def count_unread(self, user_id: uuid.UUID) -> int:
        stmt = text(
            """
            SELECT COUNT(*) FROM core.platform_announcements a
            WHERE NOT EXISTS (
                SELECT 1 FROM core.announcement_reads ar
                WHERE ar.announcement_id = a.id AND ar.user_id = :user_id
            )
            """
        )
        result = await self.session.execute(stmt, {"user_id": user_id})
        return result.scalar_one()

    async def exists(self, announcement_id: uuid.UUID) -> bool:
        stmt = select(Announcement.id).where(Announcement.id == announcement_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def mark_read(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, announcement_id: uuid.UUID) -> None:
        """UPSERT — marcar como lida uma novidade já lida é um no-op, não
        um erro (o frontend pode chamar isso toda vez que o sino abre,
        sem se preocupar em checar o estado antes)."""
        existing = await self.session.get(AnnouncementRead, {"user_id": user_id, "announcement_id": announcement_id})
        if existing is not None:
            return
        self.session.add(AnnouncementRead(tenant_id=tenant_id, user_id=user_id, announcement_id=announcement_id))
        await self.session.flush()
