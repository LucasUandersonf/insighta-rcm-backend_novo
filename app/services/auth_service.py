"""
app/services/auth_service.py

Cadastro público (self-signup) + recuperação de senha self-service.
Isolado num service próprio (não dentro do endpoint) porque as duas
operações cruzam DUAS sessões de banco distintas — algo que nenhum outro
fluxo do sistema precisa fazer, e que merece ficar centralizado e
documentado num único lugar em vez de espalhado pelo endpoint.
"""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    generate_password_reset_token,
    generate_temporary_password,
    hash_password,
)
from app.db.session import get_db_with_tenant
from app.models.password_reset_token import PasswordResetToken
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.auth_repository import AuthRepository
from app.repositories.password_reset_token_repository import PasswordResetTokenRepository
from app.repositories.tenant_repository import TenantRepository
from app.repositories.user_repository import UserRepository
from app.schemas.token import GoogleAuthResponse, RegisterRequest, TenantOption, TokenResponse
from app.services.email_client import EmailClient
from app.services.google_oauth_client import verify_google_id_token

settings = get_settings()


class AuthService:
    """Recebe uma sessão SEM tenant (get_db_no_tenant) — mesma exigência
    de AuthRepository: este é um dos poucos lugares do sistema
    legitimamente cross-tenant. Quando uma operação precisa gravar em
    tabela COM RLS (core.users), abre por conta própria uma sessão
    tenant-aware adicional (get_db_with_tenant), pontualmente, para
    aquela escrita específica."""

    def __init__(self, no_tenant_db: AsyncSession, email_client: EmailClient | None = None):
        self.no_tenant_db = no_tenant_db
        self.auth_repo = AuthRepository(no_tenant_db)
        self.reset_token_repo = PasswordResetTokenRepository(no_tenant_db)
        self.email_client = email_client or EmailClient()

    async def register(self, data: RegisterRequest) -> TokenResponse:
        """Cria a clínica (tenant) + o primeiro usuário (sempre "owner").

        DECISÃO — duas escritas, duas sessões, sem transação distribuída
        -------------------------------------------------------------------
        core.tenants não tem RLS; core.users tem (ver app/db/session.py).
        Não existe uma única sessão que sirva às duas igualmente bem: uma
        sessão "crua" não conseguiria passar pelo WITH CHECK de RLS ao
        inserir o owner; uma sessão tenant-aware precisaria SABER o
        tenant_id ANTES dele existir — o mesmo problema "ovo e galinha"
        que core.resolve_login resolve para o LOGIN. Aqui resolvemos em
        duas fases: (1) cria e comita o tenant numa sessão crua; (2) com o
        tenant_id em mãos, abre uma sessão tenant-aware nova só para
        gravar o owner. Se (2) falhar, o tenant já criado em (1) fica
        órfão (sem usuário) — aceitável para este MVP (cadastro sem
        cobrança acoplada ainda; ver DECISÃO em RegisterRequest), mas é o
        primeiro candidato a virar uma transação de verdade se o produto
        crescer para justificar o esforço (ex: SAGA/compensação).
        """
        # Duas origens possíveis para nome/e-mail/senha do owner — nunca
        # confiamos em owner_name/email enviados pelo cliente quando há
        # google_credential: eles são DERIVADOS do token, já re-verificado
        # aqui (RegisterRequest.validate_auth_method já garante que os
        # dois grupos são mutuamente exclusivos, mas a fonte de verdade do
        # e-mail em si só é estabelecida agora, na verificação).
        if data.google_credential:
            google_user = await verify_google_id_token(data.google_credential)
            owner_name = google_user.name
            owner_email = google_user.email
            # Senha aleatória, de alta entropia, que o owner nunca vê nem
            # usa — esta conta só faz login via Google. Evita adicionar
            # uma coluna nullable/"sem senha" ao model User só para este
            # caso: mais simples manter TODO usuário com uma senha
            # hasheada válida, mesmo que inatingível na prática.
            owner_password_hash = hash_password(generate_temporary_password())
        else:
            owner_name = data.owner_name
            owner_email = data.email
            owner_password_hash = hash_password(data.password)

        tenant_repo = TenantRepository(self.no_tenant_db)
        tenant = Tenant(
            id=uuid.uuid4(),
            legal_name=data.legal_name or data.trade_name,
            trade_name=data.trade_name,
            cnpj=data.cnpj,
            plan_tier=data.plan_tier,
            is_active=True,
        )
        try:
            await tenant_repo.add(tenant)
            await self.no_tenant_db.commit()
        except IntegrityError:
            await self.no_tenant_db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Já existe uma clínica cadastrada com este CNPJ.",
            )

        owner_id = uuid.uuid4()
        async for tenant_db in get_db_with_tenant(str(tenant.id)):
            user_repo = UserRepository(tenant_db)
            await user_repo.add(
                User(
                    id=owner_id,
                    tenant_id=tenant.id,
                    email=owner_email,
                    full_name=owner_name,
                    role="owner",
                    hashed_password=owner_password_hash,
                    must_change_password=False,
                )
            )

        token = create_access_token(user_id=str(owner_id), tenant_id=str(tenant.id), role="owner")
        return TokenResponse(access_token=token)

    async def login_or_signal_registration_with_google(
        self, credential: str, tenant_id: str | None = None
    ) -> GoogleAuthResponse:
        """POST /auth/google — verifica o ID token e resolve um de 3
        estados (ver docstring de GoogleAuthResponse): login direto, tela
        de seleção de clínica (mesma ambiguidade multi-tenant do login
        tradicional), ou "nenhuma conta com este e-mail" — devolvido ao
        frontend para pré-preencher o cadastro (SignUpPage.tsx), NUNCA
        criando a clínica sozinho aqui: cadastrar continua exigindo CNPJ e
        escolha de plano, que este endpoint não recebe."""
        google_user = await verify_google_id_token(credential)

        # Reaproveita resolve_login_candidates (não resolve_user_by_email)
        # porque precisamos do "role" de cada candidato para emitir o JWT
        # — resolve_user_by_email foi desenhada só para o fluxo de reset de
        # senha, que nunca precisa disso. A senha vindo nesses registros
        # simplesmente não é usada aqui.
        candidates = await self.auth_repo.resolve_login_candidates(google_user.email)
        usable = [c for c in candidates if c.is_active and c.tenant_is_active]

        if not usable:
            return GoogleAuthResponse(
                needs_registration=True,
                email=google_user.email,
                suggested_owner_name=google_user.name,
            )

        if len(usable) > 1:
            if tenant_id is not None:
                chosen = next((c for c in usable if c.tenant_id == tenant_id), None)
                if chosen is None:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Não foi possível autenticar com esta conta Google.",
                    )
                token = create_access_token(user_id=chosen.user_id, tenant_id=chosen.tenant_id, role=chosen.role)
                return GoogleAuthResponse(access_token=token)

            return GoogleAuthResponse(
                requires_tenant_selection=True,
                tenant_options=[TenantOption(tenant_id=c.tenant_id, trade_name=c.tenant_trade_name) for c in usable],
                email=google_user.email,
            )

        record = usable[0]
        token = create_access_token(user_id=record.user_id, tenant_id=record.tenant_id, role=record.role)
        return GoogleAuthResponse(access_token=token)

    async def request_password_reset(self, email: str) -> None:
        """SEMPRE silencioso: o chamador (endpoint) devolve a mesma
        resposta (202, sem corpo) não importa se o e-mail existe, está
        inativo, ou pertence a um tenant desativado — o mesmo princípio
        anti-enumeração de e-mail já aplicado no login (ver auth.py)."""
        candidates = await self.auth_repo.resolve_user_by_email(email)
        usable = [c for c in candidates if c.is_active and c.tenant_is_active]
        multi_tenant = len(usable) > 1

        for candidate in usable:
            raw_token, token_hash = generate_password_reset_token()
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES)
            await self.reset_token_repo.add(
                PasswordResetToken(
                    id=uuid.uuid4(),
                    user_id=uuid.UUID(candidate.user_id),
                    tenant_id=uuid.UUID(candidate.tenant_id),
                    token_hash=token_hash,
                    expires_at=expires_at,
                )
            )
            await self.no_tenant_db.commit()

            reset_link = f"{settings.FRONTEND_BASE_URL}/reset-password?token={raw_token}"
            clinic_note = f" na clínica {candidate.tenant_trade_name}" if multi_tenant else ""
            await self.email_client.send(
                to_email=email,
                subject=f"Redefinir senha{f' — {candidate.tenant_trade_name}' if multi_tenant else ''} — Insighta RCM",
                text_body=(
                    f"Olá, {candidate.full_name}.\n\n"
                    f"Recebemos uma solicitação para redefinir a senha da sua conta{clinic_note}.\n\n"
                    f"Clique no link abaixo para criar uma nova senha (válido por "
                    f"{settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES} minutos):\n{reset_link}\n\n"
                    "Se você não pediu isso, ignore este e-mail — sua senha continua a mesma."
                ),
            )

    async def confirm_password_reset(self, token: str, new_password: str) -> None:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        reset_token = await self.reset_token_repo.get_valid_by_hash(token_hash)
        if reset_token is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Link de redefinição inválido ou expirado.")

        async for tenant_db in get_db_with_tenant(str(reset_token.tenant_id)):
            user_repo = UserRepository(tenant_db)
            user = await user_repo.get_by_id(reset_token.user_id)
            if user is None:
                # Conta apagada entre o pedido de reset e a confirmação —
                # mesmo tratamento de "sessão/link inválido" do resto do
                # fluxo, sem detalhar o motivo exato.
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Link de redefinição inválido ou expirado.")
            user.hashed_password = hash_password(new_password)
            user.must_change_password = False
            user.password_updated_at = datetime.now(timezone.utc)
            await user_repo.save(user)

        await self.reset_token_repo.mark_used(reset_token)
        await self.no_tenant_db.commit()
