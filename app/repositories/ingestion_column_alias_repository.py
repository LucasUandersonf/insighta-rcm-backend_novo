"""
app/repositories/ingestion_column_alias_repository.py

Ver DECISÃO completa em app/sql/021_ingestion_column_aliases.sql.
"""
import uuid

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ingestion_column_alias import IngestionColumnAlias


class IngestionColumnAliasRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_mapping(self, tenant_id: uuid.UUID, data_type: str) -> dict[str, str]:
        """{source_header: canonical_field} já confirmados para este
        tenant+template — é isso que o parser CSV mescla por cima do
        cabeçalho padrão (ver csv_parser.py)."""
        stmt = select(IngestionColumnAlias.source_header, IngestionColumnAlias.canonical_field).where(
            IngestionColumnAlias.tenant_id == tenant_id, IngestionColumnAlias.data_type == data_type
        )
        result = await self.session.execute(stmt)
        return {source: field for source, field in result.all()}

    async def list_for_tenant(self, tenant_id: uuid.UUID, data_type: str) -> list[IngestionColumnAlias]:
        """Versão com o registro completo (id incluso) — alimenta a tela
        de revisão/edição, onde cada linha precisa de um id pra poder ser
        removida individualmente."""
        stmt = (
            select(IngestionColumnAlias)
            .where(IngestionColumnAlias.tenant_id == tenant_id, IngestionColumnAlias.data_type == data_type)
            .order_by(IngestionColumnAlias.source_header)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def save_many(self, tenant_id: uuid.UUID, data_type: str, mapping: dict[str, str]) -> None:
        """
        UPSERT de cada (source_header -> canonical_field) — reenviar o
        mesmo cabeçalho com um campo canônico DIFERENTE atualiza o
        mapeamento existente (o usuário mudou de ideia na revisão), nunca
        cria uma segunda linha conflitante para o mesmo cabeçalho (ver
        UNIQUE (tenant_id, data_type, source_header) na tabela).
        """
        stmt = text(
            """
            INSERT INTO core.ingestion_column_aliases (id, tenant_id, data_type, source_header, canonical_field)
            VALUES (:id, :tenant_id, :data_type, :source_header, :canonical_field)
            ON CONFLICT (tenant_id, data_type, source_header)
            DO UPDATE SET canonical_field = EXCLUDED.canonical_field
            """
        )
        for source_header, canonical_field in mapping.items():
            await self.session.execute(
                stmt,
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant_id,
                    "data_type": data_type,
                    "source_header": source_header,
                    "canonical_field": canonical_field,
                },
            )

    async def delete(self, tenant_id: uuid.UUID, alias_id: uuid.UUID) -> bool:
        stmt = sa_delete(IngestionColumnAlias).where(
            IngestionColumnAlias.tenant_id == tenant_id, IngestionColumnAlias.id == alias_id
        )
        result = await self.session.execute(stmt)
        return result.rowcount > 0
