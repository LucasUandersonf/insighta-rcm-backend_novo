"""
app/services/whatsapp_client.py

DECISÃO — mensagem por TEMPLATE pré-aprovado, não texto/documento livre
-------------------------------------------------------------------------
A WhatsApp Business Cloud API só permite que a EMPRESA inicie uma
conversa (fora de uma janela de 24h de atendimento ativo, que não existe
aqui — ninguém do lado da clínica mandou mensagem primeiro) através de um
"message template" previamente aprovado pela Meta. Enviar um PDF como
texto livre para um número que nunca iniciou conversa simplesmente falha
na API. Por isso o fluxo é: (1) fazer upload do PDF como mídia, obtendo
um media_id; (2) enviar uma mensagem de TEMPLATE cujo cabeçalho é do tipo
"document", referenciando esse media_id. O template
(`settings.WHATSAPP_REPORT_TEMPLATE_NAME`) precisa existir e estar
aprovado no Meta Business Manager ANTES de qualquer envio — isso é
configuração de conta, não código.

DECISÃO — "grupo do WhatsApp" do briefing original vira NÚMERO INDIVIDUAL
-------------------------------------------------------------------------
A API oficial da Meta não envia para grupos de WhatsApp — só para
números de telefone individuais. `tenants.whatsapp_group_id` (nome
herdado do briefing original) é tratado aqui como o número de destino
(ex: o celular do sócio/diretor responsável, ou um número de distribuição
que a diretoria acompanha). Documentado explicitamente porque muda a
expectativa do produto: não é possível, hoje, entregar num grupo com
várias pessoas simultaneamente através desta API — cada destinatário
adicional precisaria de um envio próprio.
"""
import httpx

from app.core.config import get_settings

settings = get_settings()


class WhatsAppClientError(Exception):
    pass


class WhatsAppClient:
    def __init__(self):
        if not settings.WHATSAPP_ACCESS_TOKEN or not settings.WHATSAPP_PHONE_NUMBER_ID:
            raise WhatsAppClientError("WHATSAPP_ACCESS_TOKEN/WHATSAPP_PHONE_NUMBER_ID não configurados.")
        self._base_url = f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}"
        self._phone_number_id = settings.WHATSAPP_PHONE_NUMBER_ID
        self._headers = {"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"}

    async def _upload_pdf(self, pdf_bytes: bytes, filename: str) -> str:
        url = f"{self._base_url}/{self._phone_number_id}/media"
        files = {"file": (filename, pdf_bytes, "application/pdf")}
        data = {"messaging_product": "whatsapp", "type": "application/pdf"}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=self._headers, data=data, files=files)
        if response.status_code >= 400:
            raise WhatsAppClientError(f"Falha ao enviar mídia para o WhatsApp: {response.status_code} {response.text}")
        return response.json()["id"]

    async def send_weekly_report(self, *, to_phone_number: str, pdf_bytes: bytes, filename: str) -> str:
        """Retorna o message_id retornado pela Meta em caso de sucesso."""
        media_id = await self._upload_pdf(pdf_bytes, filename)

        url = f"{self._base_url}/{self._phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone_number,
            "type": "template",
            "template": {
                "name": settings.WHATSAPP_REPORT_TEMPLATE_NAME,
                "language": {"code": "pt_BR"},
                "components": [
                    {
                        "type": "header",
                        "parameters": [{"type": "document", "document": {"id": media_id, "filename": filename}}],
                    }
                ],
            },
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=self._headers, json=payload)
        if response.status_code >= 400:
            raise WhatsAppClientError(f"Falha ao enviar template do WhatsApp: {response.status_code} {response.text}")
        return response.json()["messages"][0]["id"]
