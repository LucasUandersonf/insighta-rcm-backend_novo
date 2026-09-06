"""
app/services/support_request_service.py — Central de Ajuda, "enviar uma
pergunta sem sair do sistema". Ver DECISÃO completa em
app/sql/023_announcements_and_support.sql.

DECISÃO — sempre grava, e-mail é só um AVISO best-effort
-------------------------------------------------------------------------
Mesmo espírito de app/services/email_client.py: o pedido do cliente
nunca pode se perder por causa de e-mail mal configurado (ou ausente).
`create_request` SEMPRE persiste em `core.support_requests` primeiro;
o envio de e-mail para `settings.SUPPORT_EMAIL` acontece DEPOIS, e uma
falha nele (SMTP fora do ar, por exemplo) não derruba a resposta 201 —
o time de suporte consegue ver a pergunta na base de qualquer jeito,
mesmo perdendo o aviso em tempo real.
"""
import logging
import uuid

from app.core.config import get_settings
from app.models.support_request import SupportRequest
from app.repositories.support_request_repository import SupportRequestRepository
from app.repositories.tenant_repository import TenantRepository
from app.repositories.user_repository import UserRepository
from app.schemas.support_request import SupportRequestCreateRequest, SupportRequestResponse
from app.services.email_client import EmailClient

logger = logging.getLogger("support_request_service")
settings = get_settings()


class SupportRequestService:
    def __init__(
        self,
        repo: SupportRequestRepository,
        user_repo: UserRepository,
        tenant_repo: TenantRepository,
        email_client: EmailClient | None = None,
    ):
        self.repo = repo
        self.user_repo = user_repo
        self.tenant_repo = tenant_repo
        # Injetável para teste (monkeypatch de EmailClient.send é mais
        # simples do que instanciar aqui de propósito) — default real em
        # produção, mesmo padrão de client_factory em report_send_service.py.
        self.email_client = email_client or EmailClient()

    async def create_request(
        self, tenant_id: str, user_id: uuid.UUID, data: SupportRequestCreateRequest
    ) -> SupportRequestResponse:
        request = SupportRequest(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            user_id=user_id,
            subject=data.subject,
            message=data.message,
            status="aberto",
        )
        saved = await self.repo.add(request)

        if settings.SUPPORT_EMAIL:
            try:
                user = await self.user_repo.get_by_id(user_id)
                tenant = await self.tenant_repo.get_by_id(uuid.UUID(tenant_id))
                requester = f"{user.full_name} <{user.email}>" if user else "usuário desconhecido"
                clinic = tenant.trade_name if tenant else tenant_id
                await self.email_client.send(
                    to_email=settings.SUPPORT_EMAIL,
                    subject=f"[Central de Ajuda] {clinic} — {data.subject}",
                    text_body=f"De: {requester}\nClínica: {clinic}\n\n{data.message}",
                )
            except Exception:
                # O pedido JÁ foi persistido (linha acima) — uma falha de
                # e-mail (SMTP fora do ar, provedor rejeitando etc.) é só
                # o aviso em tempo real que se perde, nunca a pergunta em
                # si. Loga em vez de propagar, para não devolver 500 a um
                # usuário cujo pedido, na prática, já foi salvo com sucesso.
                logger.exception("Falha ao notificar SUPPORT_EMAIL para support_request_id=%s", saved.id)

        return SupportRequestResponse.model_validate(saved)

    async def list_own_tenant(self, *, limit: int = 50, offset: int = 0) -> list[SupportRequestResponse]:
        items = await self.repo.list_all(limit=limit, offset=offset)
        return [SupportRequestResponse.model_validate(i) for i in items]
