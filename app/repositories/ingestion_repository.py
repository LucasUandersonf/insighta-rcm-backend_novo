"""
app/repositories/ingestion_repository.py

DECISÃO — Idempotência via INSERT ... ON CONFLICT DO NOTHING
-------------------------------------------------------------------------
SQS garante entrega "pelo menos uma vez" (at-least-once) — o mesmo
evento de S3 pode chegar duas vezes ao worker (ex: se o worker crashar
depois de processar mas antes de deletar a mensagem da fila, ou se
rodarmos 2+ réplicas do worker). A defesa correta não é "confiar que o
worker processa uma vez só", é o BANCO garantir isso: a constraint
UNIQUE (tenant_id, s3_bucket, s3_key, s3_version_id) em
core.ingestion_files, combinada com INSERT ... ON CONFLICT DO NOTHING
RETURNING, faz o segundo worker que tentar "reclamar" o mesmo arquivo
simplesmente não conseguir — claim_file() retorna None para ele, e ele
sabe que deve pular o processamento (outro worker já está cuidando ou já
cuidou deste arquivo).
"""
import uuid

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ingestion_file import IngestionFile
from app.models.ingestion_raw_row import IngestionRawRow
from app.worker.schemas import RowParseResult


class IngestionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def claim_file(
        self,
        *,
        tenant_id: uuid.UUID,
        s3_bucket: str,
        s3_key: str,
        s3_version_id: str | None,
        file_format: str,
        original_filename: str | None = None,
        data_type: str = "faturamento",
    ) -> IngestionFile | None:
        """
        Tenta registrar o arquivo como "sendo processado agora". Se já
        existir uma linha com a mesma chave de idempotência (outro worker
        já reclamou este arquivo, nesta execução ou em uma anterior),
        retorna None e o worker deve pular o arquivo sem erro.

        `original_filename` é opcional (nulo no caminho SQS, onde a
        própria chave S3 já é o "nome do arquivo"; preenchido no caminho
        HTTP de upload — ver POST /ingestion/upload — para a tela de
        histórico poder mostrar o nome que o usuário reconhece, em vez da
        chave S3 tenant-scoped construída por baixo dos panos).

        `data_type` ("faturamento"/"agenda" — ver
        app/sql/019_agenda_ingestion.sql) NÃO faz parte da chave de
        idempotência (tenant_id, s3_bucket, s3_key, s3_version_id) — só
        descreve qual template o arquivo segue. Limitação aceita: dois
        arquivos com o MESMO nome mas `data_type` diferente colidiriam na
        idempotência (o segundo seria tratado como "já processado", não
        como um upload novo); na prática os nomes de arquivo de
        Faturamento e Agenda de um mesmo cliente tendem a já ser
        distintos (ex: "faturamento_ago.csv" vs "agenda_ago.csv"), então
        não resolvido agora.

        DECISÃO — dois alvos de ON CONFLICT diferentes, conforme
        s3_version_id é ou não None
        -----------------------------------------------------------
        A UNIQUE constraint composta (tenant_id, s3_bucket, s3_key,
        s3_version_id) de 003_ingestion_tables.sql NUNCA dispara quando
        s3_version_id é NULL nas duas linhas (semântica padrão do
        Postgres: NULL é sempre "diferente" de outro NULL numa UNIQUE
        constraint) — isso é invisível para o caminho SQS (buckets
        versionados sempre têm version_id real), mas quebraria a
        idempotência do upload HTTP contra um bucket NÃO versionado
        (s3_version_id sempre NULL). Por isso 010_ingestion_original_filename.sql
        acrescenta um índice ÚNICO PARCIAL (tenant_id, s3_bucket, s3_key)
        WHERE s3_version_id IS NULL, e usamos ELE como alvo de ON
        CONFLICT quando s3_version_id é None — a constraint original
        continua sendo o alvo quando um version_id real é informado. Os
        dois literais SQL abaixo são fixos (nunca interpolam valor de
        usuário), então não há risco de injeção na f-string.
        """
        conflict_target = (
            "(tenant_id, s3_bucket, s3_key) WHERE s3_version_id IS NULL"
            if s3_version_id is None
            else "(tenant_id, s3_bucket, s3_key, s3_version_id)"
        )
        stmt = text(
            f"""
            INSERT INTO core.ingestion_files
                (id, tenant_id, s3_bucket, s3_key, s3_version_id, file_format, status, original_filename, data_type)
            VALUES
                (:id, :tenant_id, :s3_bucket, :s3_key, :s3_version_id, :file_format, 'processing', :original_filename, :data_type)
            ON CONFLICT {conflict_target} DO NOTHING
            RETURNING id
            """
        )
        new_id = uuid.uuid4()
        result = await self.session.execute(
            stmt,
            {
                "id": new_id,
                "tenant_id": tenant_id,
                "s3_bucket": s3_bucket,
                "s3_key": s3_key,
                "s3_version_id": s3_version_id,
                "file_format": file_format,
                "original_filename": original_filename,
                "data_type": data_type,
            },
        )
        claimed_id = result.scalar_one_or_none()
        if claimed_id is None:
            return None

        await self.session.flush()
        fetched = await self.session.execute(select(IngestionFile).where(IngestionFile.id == claimed_id))
        return fetched.scalar_one()

    async def get_file_by_idempotency_key(
        self, *, tenant_id: uuid.UUID, s3_bucket: str, s3_key: str, s3_version_id: str | None
    ) -> IngestionFile | None:
        """
        Busca a IngestionFile já existente pela MESMA chave de
        idempotência usada em claim_file() — usado quando claim_file()
        retorna None (arquivo já reclamado antes) e quem chama precisa
        devolver o resultado do processamento ANTERIOR (ex: o endpoint
        HTTP de upload, para o usuário ver o que aconteceu da primeira
        vez, em vez de um erro genérico de duplicata).
        """
        stmt = select(IngestionFile).where(
            IngestionFile.tenant_id == tenant_id,
            IngestionFile.s3_bucket == s3_bucket,
            IngestionFile.s3_key == s3_key,
            IngestionFile.s3_version_id.is_(None) if s3_version_id is None else IngestionFile.s3_version_id == s3_version_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def save_raw_rows(self, ingestion_file_id: uuid.UUID, tenant_id: uuid.UUID, results: list[RowParseResult]) -> list[IngestionRawRow]:
        rows: list[IngestionRawRow] = []
        for parsed in results:
            row = IngestionRawRow(
                tenant_id=tenant_id,
                ingestion_file_id=ingestion_file_id,
                row_number=parsed.row_number,
                payload=parsed.row.model_dump(mode="json") if parsed.row else {},
                validation_errors=parsed.errors,
                status="rejected" if parsed.errors else "pending_normalization",
            )
            self.session.add(row)
            rows.append(row)
        await self.session.flush()  # necessário para os rows ganharem id antes de serem usados pela normalização
        return rows

    async def mark_processed(self, ingestion_file_id: uuid.UUID, *, row_count: int, error_row_count: int) -> None:
        await self.session.execute(
            update(IngestionFile)
            .where(IngestionFile.id == ingestion_file_id)
            .values(status="processed", row_count=row_count, error_row_count=error_row_count, processed_at=text("now()"))
        )

    async def mark_failed(self, ingestion_file_id: uuid.UUID, *, error_message: str) -> None:
        # Falha ESTRUTURAL do arquivo inteiro (ex: XML corrompido demais
        # para parsear) — diferente de erros por linha, que ficam
        # registrados em ingestion_raw_rows.validation_errors sem impedir
        # o processamento das demais linhas válidas.
        await self.session.execute(
            update(IngestionFile)
            .where(IngestionFile.id == ingestion_file_id)
            .values(status="failed", error_message=error_message, processed_at=text("now()"))
        )

    async def list_files_paginated(self, *, limit: int = 50, offset: int = 0) -> tuple[list[IngestionFile], int]:
        """
        Alimenta a tela de histórico de upload (GET /ingestion/files):
        toda IngestionFile do tenant (RLS já filtra), mais recente
        primeiro — mesmo envelope de paginação {items, total, limit,
        offset} de app/schemas/pagination.py, usado por GET /audit-log,
        GET /contracts/active e GET /denial-appeals.
        """
        total = (await self.session.execute(select(func.count()).select_from(IngestionFile))).scalar_one()
        stmt = select(IngestionFile).order_by(IngestionFile.received_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def get_by_id(self, row_id: int) -> IngestionRawRow | None:
        result = await self.session.execute(select(IngestionRawRow).where(IngestionRawRow.id == row_id))
        return result.scalar_one_or_none()

    async def list_rejected(self, *, reason: str | None = None, limit: int = 50, offset: int = 0) -> list[IngestionRawRow]:
        """
        Alimenta a tela de Setup: linhas que a Etapa 1 (validação
        estrutural) ou a Etapa 2 (normalização) não conseguiram promover
        sozinhas. `reason` filtra por validation_errors->>'reason' (ex:
        'unknown_insurance_plan') quando informado.
        """
        stmt = select(IngestionRawRow).where(IngestionRawRow.status == "rejected")
        if reason is not None:
            stmt = stmt.where(IngestionRawRow.validation_errors["reason"].astext == reason)
        stmt = stmt.order_by(IngestionRawRow.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_rejected_by_raw_value(self, raw_value: str) -> list[IngestionRawRow]:
        """
        Usado para resolução em lote: depois que o humano mapeia
        manualmente "UNIMED NAC." uma vez, todas as OUTRAS linhas
        rejeitadas com esse MESMO texto cru (provavelmente do mesmo
        arquivo diário, repetido várias vezes) podem ser promovidas juntas
        — sem isso, o usuário teria que repetir o mesmo mapeamento manual
        linha por linha.
        """
        stmt = select(IngestionRawRow).where(
            IngestionRawRow.status == "rejected",
            IngestionRawRow.validation_errors["raw_value"].astext == raw_value,
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
