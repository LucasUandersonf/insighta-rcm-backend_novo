"""
app/services/denial_appeal_draft_service.py

Rascunho de JUSTIFICATIVA de Recurso de Glosa via IA — achado do
Parecer Técnico "Boletim Insighta" (revisão 2): o maior "gap
competitivo" identificado (frente a produtos como o Rivio) era o
usuário sempre começar o parágrafo de justificativa de uma folha em
branco, mesmo quando os dados factuais do caso (motivo da negativa,
tipo de recurso, dados da guia) já estão todos no sistema.

DECISÃO — o MESMO princípio de denial_appeal_pdf_builder.py, reforçado
no PROMPT em vez de só documentado em comentário
-------------------------------------------------------------------------
"IA é o PRIMEIRO passo, nunca o ÚLTIMO": este rascunho NUNCA inventa
mérito clínico (não afirma que um procedimento era "medicamente
necessário" além do que os dados factuais já sustentam, não inventa
jurisprudência, não cita lei/resolução que não foi fornecida). O prompt
restringe a IA a argumentar SOMENTE a partir do que
get_document_context() já devolve — motivo da negativa, dados da guia,
convênio, procedimento, CID, prazo — e a pedir explicitamente para o
usuário completar com documentação clínica quando o caso exigir. O
texto SEMPRE volta como preview editável (nunca é gravado/protocolado
sozinho) — o mesmo padrão de human-in-the-loop de
contract_extraction_service.py, adaptado de "extrair dado estruturado"
para "redigir um parágrafo grounded em dado estruturado".

Mesma separação de contract_extraction_service.py: a parte "isso é
seguro o bastante pra virar prompt" é uma função PURA e testável
(`build_draft_user_message`), separada da chamada de rede
(`AnthropicDenialAppealDrafter`).
"""
import httpx

from app.core.config import get_settings

settings = get_settings()

_APPEAL_TYPE_LABELS = {
    "tecnica": "recurso técnico (erro de preenchimento pós-envio)",
    "administrativa": "recurso administrativo (glosa documental)",
    "medica": "recurso médico (negativa de cobertura por junta médica)",
}

_DRAFT_SYSTEM_PROMPT = """Você ajuda uma clínica médica brasileira a redigir o parágrafo de \
"Justificativa do Recurso" de uma carta de contestação de glosa (negativa de pagamento) enviada por \
uma operadora de saúde.

REGRAS QUE VOCÊ NUNCA PODE QUEBRAR:
1. Use APENAS os fatos fornecidos abaixo (motivo da negativa, tipo de recurso, dados da guia/procedimento, \
CID, convênio, valor, prazo). NUNCA invente fatos clínicos, jurisprudência, número de lei, resolução da ANS \
ou cláusula contratual que não foi fornecida explicitamente a você.
2. NUNCA afirme que um procedimento era "medicamente necessário" ou defenda o mérito clínico do atendimento \
além do que os fatos já sustentam — isso exige o julgamento de um profissional de saúde, que você não tem \
como substituir com segurança.
3. Se o motivo da negativa for de natureza ADMINISTRATIVA/documental (ex: falta de guia, senha vencida, \
divergência de código), construa o argumento em cima disso (ex: "a guia foi corretamente autorizada sob o \
número X, senha Y" quando esses dados existirem).
4. Se os fatos fornecidos não forem suficientes para sustentar um argumento substantivo (faltam dados-chave \
ou a negativa é de mérito clínico que você não pode avaliar), escreva um parágrafo mais curto reconhecendo \
os fatos disponíveis e sinalizando EXPLICITAMENTE, dentro do próprio texto, que a clínica precisa completar \
com a documentação clínica/jurídica específica do caso antes de protocolar.
5. Tom formal, em português do Brasil, como um parágrafo de carta de contestação — sem saudação/despedida \
(isso já existe no documento), só o corpo da justificativa. Sem markdown, sem lista, texto corrido.
6. Devolva APENAS o parágrafo, sem nenhum texto antes ou depois."""


class DenialAppealDraftError(Exception):
    pass


def build_draft_user_message(context_row: dict) -> str:
    """
    Função PURA: monta a mensagem enviada à IA a partir do MESMO dict
    que `DenialAppealRepository.get_document_context` já devolve para o
    PDF (ver denial_appeal_service.build_appeal_document) — nenhum dado
    novo, só reformatado em texto para o prompt. Testável sem rede.
    """
    appeal_type_label = _APPEAL_TYPE_LABELS.get(context_row["appeal_type"], context_row["appeal_type"])
    lines = [
        f"Tipo de recurso: {appeal_type_label}.",
        f"Convênio: {context_row.get('insurance_plan_name') or 'não informado'}.",
        f"Motivo da negativa informado pela operadora: {context_row.get('operator_denial_reason') or 'não informado'}.",
    ]
    if context_row.get("procedure_code"):
        lines.append(f"Código do procedimento: {context_row['procedure_code']}.")
    if context_row.get("cid_code"):
        lines.append(f"CID: {context_row['cid_code']}.")
    if context_row.get("guia_tipo") or context_row.get("guia_numero") or context_row.get("guia_senha"):
        guia_bits = []
        if context_row.get("guia_tipo"):
            guia_bits.append(f"tipo {context_row['guia_tipo']}")
        if context_row.get("guia_numero"):
            guia_bits.append(f"número {context_row['guia_numero']}")
        if context_row.get("guia_senha"):
            guia_bits.append(f"senha de autorização {context_row['guia_senha']}")
        lines.append(f"Guia TISS vinculada: {', '.join(guia_bits)}.")
    else:
        lines.append("Guia TISS vinculada: não há guia cadastrada para este atendimento.")
    if context_row.get("charged_value") is not None:
        lines.append(f"Valor cobrado: R$ {float(context_row['charged_value']):,.2f}.".replace(",", "X").replace(".", ",").replace("X", "."))

    lines.append(
        "Com base SOMENTE nesses fatos, escreva o parágrafo de justificativa do recurso, seguindo todas as "
        "regras do system prompt."
    )
    return "\n".join(lines)


class AnthropicDenialAppealDrafter:
    """Implementação real via API da Anthropic (Messages API) — mesmo
    padrão de AnthropicContractExtractor em contract_extraction_service.py
    (injetada por duck typing, então um teste pode trocar por um fake
    sem tocar rede)."""

    def __init__(self):
        if not settings.ANTHROPIC_API_KEY:
            raise DenialAppealDraftError("ANTHROPIC_API_KEY não configurada.")
        self._headers = {
            "x-api-key": settings.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    async def draft(self, context_row: dict) -> str:
        payload = {
            "model": settings.DENIAL_APPEAL_DRAFT_MODEL,
            "max_tokens": 1024,
            "system": _DRAFT_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": build_draft_user_message(context_row)}],
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post("https://api.anthropic.com/v1/messages", headers=self._headers, json=payload)
        if response.status_code >= 400:
            raise DenialAppealDraftError(f"Falha ao gerar rascunho via IA: {response.status_code} {response.text}")

        body = response.json()
        text = "".join(block.get("text", "") for block in body.get("content", [])).strip()
        if not text:
            raise DenialAppealDraftError("A IA devolveu uma resposta vazia — tente novamente.")
        return text
