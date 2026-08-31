"""
app/services/contract_extraction_service.py

Parser Inteligente de Contratos: extrai a tabela de preços de um PDF de
contrato de convênio via LLM estruturada. Segue o mesmo princípio de
denial_risk_engine.py — a parte que decide "isso é confiável?" é uma
função PURA e testável (`validate_extracted_items`), separada da
chamada de rede (`AnthropicContractExtractor`), exatamente como
whatsapp_client.py separa "montar payload" de "fazer a chamada HTTP".

DECISÃO — IA é o PRIMEIRO passo, nunca o ÚLTIMO
-------------------------------------------------------------------------
O resultado desta extração NUNCA é persistido direto em contract_items.
Ele volta como preview (ContractExtractionResponse) para a Tela de
Conferência (Human-in-the-Loop) — só ContractIntakeService.homologate(),
chamado depois que um humano confirma/corrige, grava de verdade. Isso é
uma decisão de produto, não só técnica: em RCM médico, um preço errado
processado silenciamente por semanas é dinheiro real perdido ou uma
cobrança indevida — a IA acelera a digitação, não substitui o
julgamento humano final.

DECISÃO — validação de formato TUSS é regra de negócio, não capricho
-------------------------------------------------------------------------
Código TUSS (Terminologia Unificada da Saúde Suplementar) é numérico,
tipicamente 8 dígitos. Uma extração que devolve "consulta" no lugar de
"10101012" é um sinal de que a IA "alucinou" a estrutura da tabela (ex:
confundiu coluna de código com coluna de descrição) — melhor marcar como
aviso para o humano conferir do que aceitar cegamente.
"""
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import httpx

from app.core.config import get_settings

settings = get_settings()

_TUSS_CODE_PATTERN = re.compile(r"^\d{6,10}$")

_EXTRACTION_SYSTEM_PROMPT = """Você é um extrator de dados de contratos de convênios médicos brasileiros.
Receberá o texto de um PDF de tabela de preços (contrato de repasse entre uma clínica e uma operadora de saúde).
Devolva APENAS um JSON estrito, sem nenhum texto antes ou depois, no formato:
{"items": [{"tuss_code": "10101012", "procedure_name": "Consulta em consultório", "agreed_price": 150.00}, ...]}
Regras:
- tuss_code: só dígitos, sem pontuação.
- agreed_price: número decimal, sem símbolo de moeda.
- Se não conseguir identificar o código TUSS de uma linha, OMITA a linha (não invente um código).
- Não inclua nenhuma explicação, markdown ou texto fora do JSON."""


class ContractExtractionError(Exception):
    pass


@dataclass
class ExtractedItem:
    tuss_code: str
    procedure_name: str | None
    agreed_price: float
    warning: str | None = None


@dataclass
class ExtractionResult:
    items: list[ExtractedItem] = field(default_factory=list)
    # Avisos GERAIS da extração (não de uma linha específica) — ex: "N
    # linhas descartadas por código TUSS inválido".
    warnings: list[str] = field(default_factory=list)


def validate_extracted_items(raw_items: list[dict]) -> ExtractionResult:
    """
    Função PURA: recebe a lista de dicts já parseada do JSON devolvido
    pela IA (ou de qualquer outra fonte — inclusive um CSV de fallback
    manual) e decide o que é confiável o bastante para virar preview,
    o que vira preview COM aviso, e o que é descartado.

    Nunca lança exceção por dado ruim — dado ruim vira warning, porque
    quem decide "descarta essa linha ou corrige" é o humano na Tela de
    Conferência, não este código.
    """
    result = ExtractionResult()
    seen_codes: set[str] = set()
    discarded_invalid_code = 0
    discarded_invalid_price = 0

    for raw in raw_items:
        tuss_code = str(raw.get("tuss_code", "")).strip()
        tuss_code = re.sub(r"[^\d]", "", tuss_code)  # sanitiza pontuação residual ("101.01.012" -> "10101012")

        if not tuss_code:
            discarded_invalid_code += 1
            continue

        try:
            agreed_price = float(Decimal(str(raw.get("agreed_price", "")).replace(",", ".")))
        except (InvalidOperation, ValueError, TypeError):
            discarded_invalid_price += 1
            continue

        if agreed_price <= 0:
            discarded_invalid_price += 1
            continue

        warning = None
        if not _TUSS_CODE_PATTERN.match(tuss_code):
            warning = "Código TUSS fora do padrão esperado (6 a 10 dígitos) — confira antes de homologar."

        if tuss_code in seen_codes:
            # Mesmo código apareceu duas vezes na extração — mantém a
            # ÚLTIMA ocorrência (linhas mais abaixo num PDF costumam ser
            # "tabela vigente" quando há reajuste anunciado no topo) e
            # avisa, em vez de silenciosamente descartar uma das duas.
            result.items = [i for i in result.items if i.tuss_code != tuss_code]
            result.warnings.append(f"Código TUSS {tuss_code} apareceu mais de uma vez na extração — mantida a última ocorrência.")

        seen_codes.add(tuss_code)
        result.items.append(
            ExtractedItem(
                tuss_code=tuss_code,
                procedure_name=(str(raw["procedure_name"]).strip() if raw.get("procedure_name") else None),
                agreed_price=round(agreed_price, 2),
                warning=warning,
            )
        )

    if discarded_invalid_code:
        result.warnings.append(f"{discarded_invalid_code} linha(s) descartada(s) por não ter código TUSS identificável.")
    if discarded_invalid_price:
        result.warnings.append(f"{discarded_invalid_price} linha(s) descartada(s) por valor de preço inválido.")
    if not result.items:
        result.warnings.append("Nenhum item extraído com confiança — cadastre a tabela manualmente ou tente novo upload.")

    return result


class AnthropicContractExtractor:
    """Implementação real via API da Anthropic (Messages API). Injetada
    no service por interface (duck typing — só precisa expor `extract`),
    então um teste pode passar um extrator falso sem tocar rede, mesmo
    fora deste arquivo."""

    def __init__(self):
        if not settings.ANTHROPIC_API_KEY:
            raise ContractExtractionError("ANTHROPIC_API_KEY não configurada.")
        self._headers = {
            "x-api-key": settings.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    async def extract(self, pdf_text: str) -> ExtractionResult:
        import json

        payload = {
            "model": settings.CONTRACT_EXTRACTION_MODEL,
            "max_tokens": 4096,
            "system": _EXTRACTION_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": pdf_text[:100_000]}],  # limite defensivo de tamanho de prompt
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages", headers=self._headers, json=payload
            )
        if response.status_code >= 400:
            raise ContractExtractionError(f"Falha na extração via IA: {response.status_code} {response.text}")

        body = response.json()
        raw_text = "".join(block.get("text", "") for block in body.get("content", []))
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ContractExtractionError("A IA devolveu um JSON inválido — tente novo upload.") from exc

        return validate_extracted_items(parsed.get("items", []))
