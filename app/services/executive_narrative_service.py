"""
app/services/executive_narrative_service.py

Resumo executivo narrado por IA (Sala de Comando) — pedido direto do
usuário: "a IA seria o Jarvis pegando os nossos cálculos e transformando
em texto explicativo... o gestor teria a impressão de um sistema vivo".

Mesmo princípio de denial_risk_engine.py e contract_extraction_service.py:
a parte que DECIDE os fatos (KPIs, insights) já existe e é 100%
determinística (AnalyticsService.get_executive_summary/get_smart_insights)
— este módulo só faz a IA ESCREVER sobre fatos já calculados, nunca
calcular nada sozinha. Mesma separação de contract_extraction_service.py:
uma função PURA que monta o prompt a partir de fatos já formatados
(`build_narrative_prompt`), separada da chamada de rede
(`AnthropicNarrativeGenerator`).

DECISÃO — grounding: só números PRÉ-FORMATADOS entram no prompt, nunca
floats crus
-------------------------------------------------------------------------
Cada fato vira uma linha de texto já pronta (ex: "Faturamento bruto: R$
12.345,67"), na MESMA formatação que o resto do produto já usa (ver
smart_insights_engine.py). Isso reduz a chance de a IA "recalcular" ou
arredondar um número sozinha — ela só precisa COPIAR a linha que já
está pronta, nunca fazer conta. O prompt instrui explicitamente:
nenhum número fora desta lista pode aparecer na resposta. Mesmo
princípio de "nunca inventa confiança que a evidência não dá" do resto
do produto — aqui aplicado a texto gerado, não a um score.

DECISÃO — reaproveita as frases de smart_insights_engine.py, não pede
pra IA reler os números crus
-------------------------------------------------------------------------
`SmartInsightResponse.title`/`.message` já são texto em português,
revisado, com a formatação de moeda correta, escrito por
smart_insights_engine.py (motor determinístico, sem IA). Passar essas
frases prontas para o narrador (em vez de reexpor os floats brutos que
as geraram) significa que a IA está literalmente reescrevendo/
sintetizando conteúdo já correto, nunca inventando uma leitura nova dos
números — o risco de alucinação cai para "parafrasear errado uma frase
dada", não "inventar um fato".
"""
import logging
from dataclasses import dataclass, field
from datetime import date

import httpx

from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger("executive_narrative_service")

# Poucas frases (as de maior impacto — generate_insights já ordena assim)
# bastam para dar contexto: o objetivo é um resumo de 2-4 frases, não uma
# leitura de todo o feed.
_MAX_INSIGHT_LINES = 4

_NARRATION_SYSTEM_PROMPT = """Você é um consultor financeiro que escreve o resumo executivo diário de uma clínica médica brasileira, para o(a) gestor(a) ler ao abrir a tela.

REGRAS RÍGIDAS (nunca quebre nenhuma):
1. Use SOMENTE os fatos e números listados abaixo, exatamente como estão escritos. NUNCA calcule, estime, arredonde diferente ou invente nenhum valor, percentual, prazo, nome de convênio ou comparação que não esteja explicitamente na lista.
2. Se um assunto não está na lista de fatos, não fale sobre ele — nunca preencha lacuna com suposição.
3. Escreva de 2 a 4 frases corridas, em português do Brasil, tom direto e profissional (como um consultor de confiança falando com o gestor, nunca robótico ou genérico).
4. Nunca use markdown, listas, títulos ou emojis — só texto corrido, pronto para aparecer direto na tela.
5. Não repita as frases de entrada literalmente, palavra por palavra — sintetize a situação, priorizando o que for mais urgente ou financeiramente relevante entre os fatos dados.
6. Nunca compare com "o mercado", "a média do setor" ou "outras clínicas" a menos que isso já esteja explícito nos fatos fornecidos.

Devolva APENAS o texto do resumo, sem nenhuma explicação antes ou depois."""


class NarrativeGenerationError(Exception):
    pass


@dataclass
class NarrativeFacts:
    """Fatos JÁ CALCULADOS (por AnalyticsService, sem IA nenhuma) que
    alimentam a narrativa — ver DECISÃO no topo do módulo sobre por que
    tudo aqui já chega pré-formatado como string, nunca como float cru."""

    period_start: date
    period_end: date
    total_billed: float
    financial_hole: float
    payment_gap: float
    denial_at_risk_value: float
    avg_days_to_receive: float | None
    # Já formatadas por smart_insights_engine.py ("título: mensagem"),
    # ordenadas por impacto (a mais relevante primeiro) — ver DECISÃO
    # acima sobre reaproveitar texto já revisado em vez de floats crus.
    insight_lines: list[str] = field(default_factory=list)


def _format_currency(value: float) -> str:
    # Mesma convenção de smart_insights_engine.py (f"R$ {value:,.2f}") —
    # reaproveitada aqui de propósito para o texto que o modelo recebe
    # usar EXATAMENTE a mesma grafia que o resto do produto já usa.
    return f"R$ {value:,.2f}"


def build_narrative_prompt(facts: NarrativeFacts) -> str:
    """
    Função PURA — monta a lista de fatos pré-formatados que vai no corpo
    da mensagem para a IA. Nenhuma chamada de rede aqui (ver
    AnthropicNarrativeGenerator), só formatação determinística —
    testável sem tocar a API.
    """
    period_label = f"{facts.period_start.strftime('%d/%m')} a {facts.period_end.strftime('%d/%m/%Y')}"
    lines = [
        f"Período analisado: {period_label}",
        f"Faturamento bruto: {_format_currency(facts.total_billed)}",
        f"Perda por divergência de cobrança (cobrado abaixo do contrato): {_format_currency(facts.financial_hole)}",
        f"Perda por divergência de recebimento (operadora pagou abaixo do contratado): {_format_currency(facts.payment_gap)}",
        f"Valor faturado em risco de glosa: {_format_currency(facts.denial_at_risk_value)}",
    ]
    if facts.avg_days_to_receive is not None:
        lines.append(f"Prazo médio de recebimento: {facts.avg_days_to_receive:.0f} dias")

    top_insights = facts.insight_lines[:_MAX_INSIGHT_LINES]
    if top_insights:
        lines.append("Insights identificados no período, do mais para o menos importante:")
        lines.extend(f"- {line}" for line in top_insights)

    return "\n".join(lines)


class AnthropicNarrativeGenerator:
    """Implementação real via API da Anthropic (Messages API) — mesmo
    padrão de AnthropicContractExtractor (contract_extraction_service.py):
    raw httpx (não o SDK oficial), pois é o único integrador de IA já
    existente neste projeto e mantém as duas chamadas consistentes entre
    si. Injetada por interface (duck typing — só precisa expor
    `generate`), então um teste pode passar um gerador falso sem tocar
    rede."""

    def __init__(self):
        if not settings.ANTHROPIC_API_KEY:
            raise NarrativeGenerationError("ANTHROPIC_API_KEY não configurada.")
        self._headers = {
            "x-api-key": settings.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    async def generate(self, facts_text: str) -> str:
        payload = {
            "model": settings.EXECUTIVE_NARRATIVE_MODEL,
            "max_tokens": 400,
            "system": _NARRATION_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": facts_text}],
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages", headers=self._headers, json=payload
            )
        if response.status_code >= 400:
            raise NarrativeGenerationError(f"Falha ao gerar narrativa via IA: {response.status_code} {response.text}")

        body = response.json()
        text = "".join(block.get("text", "") for block in body.get("content", [])).strip()
        if not text:
            raise NarrativeGenerationError("A IA devolveu uma resposta vazia.")
        return text
