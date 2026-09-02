"""
app/services/email_client.py

Cliente de e-mail transacional genérico via SMTP — usado hoje só pelo
fluxo de recuperação de senha (app/services/auth_service.py). Deixado
"semi-pronto" de propósito, a pedido explícito do usuário: o FLUXO
inteiro (token, expiração, endpoint de confirmação, template de e-mail)
já funciona de ponta a ponta; só falta um provedor SMTP de verdade
configurado (SMTP_HOST/SMTP_USERNAME/SMTP_PASSWORD em app/core/config.py)
para o e-mail sair de fato — até lá, cada envio vira um log estruturado
com o conteúdo que teria sido enviado, para dar para testar o fluxo em
desenvolvimento sem depender de nenhuma conta externa.

DECISÃO — smtplib (stdlib) + asyncio.to_thread, não um SDK de provedor
-------------------------------------------------------------------------
Um SDK específico (ex: sendgrid-python, boto3 para SES) amarraria o
projeto a UM provedor antes mesmo dele ser escolhido. SMTP é o único
protocolo que todo provedor transacional relevante suporta (SendGrid,
Mailgun, AWS SES, Postmark, Gmail/Workspace...) — trocar de provedor no
futuro é só trocar as variáveis de ambiente, nunca o código. `smtplib` é
síncrono (bloqueia a thread); `asyncio.to_thread` empresta uma thread do
pool padrão do Python para não travar o event loop do FastAPI enquanto o
envio acontece — evita puxar `aiosmtplib` como nova dependência só para
um único caso de uso ainda de baixo volume (recuperação de senha).
"""
import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings

logger = logging.getLogger("email_client")
settings = get_settings()


class EmailClient:
    @property
    def is_configured(self) -> bool:
        return bool(settings.SMTP_HOST and settings.SMTP_USERNAME and settings.SMTP_PASSWORD)

    async def send(self, *, to_email: str, subject: str, text_body: str, html_body: str | None = None) -> None:
        if not self.is_configured:
            # Degradação graciosa (mesmo padrão de SENTRY_DSN/ANTHROPIC_API_KEY
            # ausentes) — nunca derruba a requisição por falta de provedor
            # configurado. O e-mail completo vai pro log para dar de testar
            # o fluxo sem SMTP real ainda.
            logger.warning(
                "SMTP não configurado — e-mail NÃO enviado de verdade. "
                "Configure SMTP_HOST/SMTP_USERNAME/SMTP_PASSWORD para ativar o envio real.\n"
                "Para: %s\nAssunto: %s\nCorpo:\n%s",
                to_email,
                subject,
                text_body,
            )
            return

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL}>"
        message["To"] = to_email
        message.set_content(text_body)
        if html_body:
            message.add_alternative(html_body, subtype="html")

        await asyncio.to_thread(self._send_sync, message)

    def _send_sync(self, message: EmailMessage) -> None:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
            if settings.SMTP_USE_TLS:
                smtp.starttls()
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            smtp.send_message(message)
