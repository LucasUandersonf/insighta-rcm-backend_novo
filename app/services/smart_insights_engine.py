"""
app/services/smart_insights_engine.py

Painel de Insights Descritivos e Acionáveis (Sprint de Dashboards de
Decisão): traduz números agregados em texto e recomendação, priorizado
por impacto financeiro. Mesmo princípio de denial_risk_engine.py — regras
determinísticas e explicáveis (não um modelo de ML de caixa-preta): a
diretoria precisa conseguir responder "por que esse alerta apareceu?"
com uma frase objetiva.

Todas as funções aqui são PURAS (recebem dataclasses já calculados,
nunca tocam banco) — testáveis isoladamente, sem Postgres, como
test_denial_risk_engine.py e test_no_show_risk_engine.py fazem para os
outros motores.

DECISÃO — reescrita de linguagem (UX writing), pedido explícito do
usuário
-------------------------------------------------------------------------
Esta versão troca TODO o texto de cada insight por uma redação sem
jargão técnico, testada contra o critério "uma pessoa sem nenhum
conhecimento do setor precisa entender o problema E saber o que fazer
só de ler o card" — nunca "glosa" sem explicar o que é na mesma frase,
nunca uma sigla sem tradução (nem "CRM": trocado por "buscar pacientes
novos ou trazer de volta quem já foi cliente"), sempre terminando numa
frase de ação concreta, não em "recomendamos revisar" genérico. Cada
Insight também ganha `action_label`/`action_href` (ver dataclass abaixo)
— o frontend transforma isso num botão real dentro do card, fechando o
ciclo "li o problema -> cliquei -> resolvi", em vez de deixar o usuário
navegar sozinho até achar a tela certa.

DECISÃO — consolidação por convênio em `_denial_spike_insights`
-------------------------------------------------------------------------
Antes, cada COMBINAÇÃO (convênio, motivo de glosa) virava um card
próprio — uma clínica com 3 motivos diferentes de glosa na mesma
operadora via 3 cards quase idênticos empilhados no feed (achado real,
testado com dado sintético: uma conta chegou a mostrar 6 cards de
"Bradesco Saúde" ao mesmo tempo). Isso é exatamente o "encher
linguiça" que o produto inteiro tenta evitar. Agora é 1 card por
CONVÊNIO, juntando todos os motivos que dispararam — o motivo mais
frequente vira a manchete, os demais entram como "e mais N motivo(s)".
"""
from dataclasses import dataclass, field

from app.services.report_calculations import compute_roi_pct
from app.services.threshold_calibration import compute_percentile_pair

# Tradução em português simples de cada motivo técnico do motor de glosa
# (denial_risk_engine.py) — usada SÓ na composição de frases deste
# arquivo (não é a fonte de verdade do reason_code em si, que continua
# vivendo em denial_risk_engine.py).
_REASON_PLAIN = {
    "missing_cid": "faltou o código da doença (CID) no atendimento",
    "missing_procedure_code": "faltou o código do procedimento realizado",
    "no_contract_reference": "esse convênio ainda não tem uma tabela de preços cadastrada no sistema",
    "value_above_contract": "o valor cobrado ficou mais alto do que o combinado no contrato",
    "value_below_contract_revenue_leak": "o valor cobrado ficou mais baixo do que o combinado no contrato",
    "duplicate_billing": "esse atendimento já tinha sido cobrado antes, com o mesmo valor (duplicidade)",
}

# Amostra mínima antes de declarar uma variação percentual "spike" —
# mesma lógica de MIN_SAMPLE_SIZE em no_show_risk_engine.py: 2 casos indo
# para 3 é "+50%" mas não é um padrão, é ruído estatístico.
_MIN_SAMPLE_FOR_TREND = 3
_SPIKE_THRESHOLD_PCT = 15.0
_HIGH_RISK_NO_SHOW_ALERT_THRESHOLD = 5
_UTILIZATION_DROP_ALERT_PP = 10.0  # pontos percentuais
# Mesmo piso de "agenda livre" usado em occupancyBarClass/occupancyNote
# (ExecutiveAgendaSummary.tsx, frontend) — para o texto do insight NUNCA
# nomear alguém que o painel de apoio logo abaixo não classificaria como
# "com a agenda livre" (as duas leituras precisam bater).
_IDLE_PROFESSIONAL_UTILIZATION_THRESHOLD = 0.6

# Auditoria Go-Live — redesenho "menos BI" do painel: dois novos insights
# textuais, mesmo padrão de threshold nomeado e amostra mínima dos demais.
_WEEKDAY_LABELS = ("domingo", "segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado")
_MIN_WEEKDAY_SAMPLE = 3  # mesmo raciocínio de _MIN_SAMPLE_FOR_TREND: amostra baixa vira ruído, não padrão
_WEEKDAY_DROP_CRITICAL_PCT = 30.0
_WEEKDAY_DROP_WARNING_PCT = 15.0
_DENIAL_RISK_PCT_CRITICAL = 40.0
_DENIAL_RISK_PCT_WARNING = 15.0

# Amostra mínima de MESES de histórico antes de sugerir um limiar de
# risco de glosa calibrado pela própria clínica (ver DECISÃO completa em
# threshold_calibration.py — Épico F2.1 do Plano Diretor). Mais baixa que
# MIN_PATIENTS_FOR_SUGGESTION do no-show (10) porque a unidade aqui é
# "mês", não "paciente" — pedir 10 meses de histórico antes de qualquer
# sugestão adiaria demais um recurso que já é opcional (o tenant só vê
# essa sugestão se pedir).
MIN_MONTHS_FOR_DENIAL_RISK_SUGGESTION = 6


@dataclass
class DenialRiskThresholdSuggestion:
    warning_threshold: float
    critical_threshold: float
    sample_size: int  # quantos meses de histórico entraram no cálculo


def resolve_denial_risk_thresholds(tenant) -> tuple[float, float]:
    """Duck-typed de propósito (mesmo padrão de
    no_show_risk_engine.resolve_thresholds): aceita qualquer objeto com
    `denial_risk_warning_threshold`/`denial_risk_critical_threshold`
    (Decimal/float/None) ou `None` — centraliza a conversão "None -> default
    do módulo" para não divergir entre quem chama."""
    if tenant is None:
        return _DENIAL_RISK_PCT_WARNING, _DENIAL_RISK_PCT_CRITICAL
    warning = (
        float(tenant.denial_risk_warning_threshold)
        if tenant.denial_risk_warning_threshold is not None
        else _DENIAL_RISK_PCT_WARNING
    )
    critical = (
        float(tenant.denial_risk_critical_threshold)
        if tenant.denial_risk_critical_threshold is not None
        else _DENIAL_RISK_PCT_CRITICAL
    )
    return warning, critical


def suggest_denial_risk_thresholds(monthly_denial_risk_pcts: list[float]) -> DenialRiskThresholdSuggestion | None:
    """
    Sugere `denial_risk_warning_threshold`/`denial_risk_critical_threshold`
    a partir da distribuição REAL de risco de glosa mês a mês desta
    clínica (escala 0-100, mesma de `InsightsPeriodInput.denial_risk_pct`)
    — não um corte genérico igual pra qualquer clínica. Mesmo raciocínio
    de no_show_risk_engine.suggest_thresholds: mediana vira o aviso
    ("comportamento típico já merece atenção"), P85 vira o crítico (só os
    15% piores meses da própria clínica).

    Retorna None com menos de MIN_MONTHS_FOR_DENIAL_RISK_SUGGESTION meses
    qualificados — mesma cautela de nunca inventar confiança que a
    evidência não dá.
    """
    pair = compute_percentile_pair(
        monthly_denial_risk_pcts, min_sample=MIN_MONTHS_FOR_DENIAL_RISK_SUGGESTION, high_percentile=85
    )
    if pair is None:
        return None
    warning, critical = pair.median, pair.high
    # Defesa: distribuição concentrada pode fazer P85 empatar/ficar abaixo
    # da mediana — o motor exige warning < critical (mesma regra de
    # TenantService.update_own_tenant), nunca sugerimos um par inválido.
    if critical <= warning:
        critical = min(warning + 1.0, 99.0)
    return DenialRiskThresholdSuggestion(
        warning_threshold=round(warning, 1), critical_threshold=round(critical, 1), sample_size=pair.sample_size
    )

# Achado do usuário sobre lacunas do módulo de Agenda: volume por dia da
# semana (weekday_appointment_counts acima) não responde "quinta tem taxa
# de falta alta" — só "quinta tem menos gente marcado". Comparação é
# INTRA-período (o dia contra a MÉDIA do próprio período), não período
# a período como _weekday_drop_insight: um corte absoluto (ex: "acima de
# 30%") não se adapta ao perfil de cada clínica/especialidade, mas "este
# dia está X pontos acima da sua própria média" é sempre acionável.
_WEEKDAY_NO_SHOW_RATE_CRITICAL_PP = 20.0  # pontos percentuais acima da média do período
_WEEKDAY_NO_SHOW_RATE_WARNING_PP = 10.0

# Terceiro exemplo do briefing de redesenho: meta anual vs. ritmo real.
# "Atrás do ritmo" é medido contra o esperado NA DATA DE HOJE (meta *
# fração do ano decorrida), não contra a meta inteira — do contrário
# todo dia antes de 31/dez estaria "abaixo da meta" por definição.
_ANNUAL_GOAL_BEHIND_WARNING_PCT = 10.0
_ANNUAL_GOAL_BEHIND_CRITICAL_PCT = 25.0

# Novos insights de Agenda (achado do Dicionário de Dados: booking_channel/
# cancellation_reason são campos novos do Template de Agenda) — mesmo
# critério de amostra mínima nomeada do resto do arquivo.
#
# Achado 7 da Auditoria de Templates e Insights (baixo) — TODOS os
# limiares abaixo (e os de OPME/coparticipação, mais adiante neste
# arquivo) são constantes fixas "no chute", do mesmo jeito que o resto
# deste motor já fazia antes desta rodada (ex.: _SPIKE_THRESHOLD_PCT).
# Não calibramos contra distribuição real porque simplesmente não existe
# volume de produção suficiente ainda para isso — CALIBRAR sem dado real
# seria só troca um número arbitrário por outro igualmente arbitrário
# (e mais perigoso: um que parece "baseado em análise" sem estar). Fica
# registrado aqui como item de revisão explícito: quando houver volume
# real de uso destes 2 insights (canal/motivo), revisitar estes 4
# valores olhando a distribuição de verdade, não arbitrando um novo
# corte às cegas.
_MIN_CHANNEL_NO_SHOW_SAMPLE = 5  # amostra mínima tanto do canal quanto do total geral
_CHANNEL_NO_SHOW_RATE_CRITICAL_PP = 20.0  # pontos percentuais acima da média geral
_CHANNEL_NO_SHOW_RATE_WARNING_PP = 10.0
_MIN_CANCELLATION_SAMPLE = 5  # mesmo raciocínio: poucos cancelamentos, "70% do mesmo motivo" é ruído
_CANCELLATION_REASON_CONCENTRATION_CRITICAL_PCT = 60.0
_CANCELLATION_REASON_CONCENTRATION_WARNING_PCT = 40.0

# Novos insights de Faturamento (achado do Dicionário de Dados: item_type/
# coparticipation_value são campos novos do Template de Faturamento).
#
# Achado 4 da Auditoria de Templates e Insights (médio) — a versão
# original destes 2 insights disparava sempre que a condição estática
# fosse satisfeita, sem comparar contra o período anterior. Uma clínica
# de ortopedia tem proporção de OPME estruturalmente alta — o card
# apareceria em TODO carregamento do painel, para sempre, virando ruído
# (o próprio "encher linguiça" que este arquivo já documenta evitar no
# topo). A correção usa o MESMO padrão já aplicado ao resto do motor:
# _financial_hole_insight/_payment_gap_insight/_value_saved_insight só
# destacam quando o número piora ou melhora, não quando é estruturalmente
# normal para aquela clínica.
#
# Achado 7 da Auditoria (baixo) — os 4 números abaixo (piso de OPME,
# aumento mínimo em pp, amostra mínima de coparticipação) têm a MESMA
# limitação já registrada no bloco de canal/motivo, mais acima neste
# arquivo: constantes fixas, não calibradas contra dado real de
# produção. Mesmo item de revisão futura, não repetido em detalhe aqui.
_OPME_CONCENTRATION_MIN_PCT = 15.0  # piso: só relevante se já for uma fatia material do faturamento
_OPME_CONCENTRATION_INCREASE_PP = 5.0  # só alerta se SUBIU pelo menos isso vs. o período anterior
# Amostra mínima de billings COM coparticipação preenchida antes de
# declarar o dado "confiável o bastante pra virar card" — mesmo
# raciocínio de amostra mínima do resto do arquivo (1-2 linhas isoladas
# não provam que o cliente já preenche essa coluna de forma consistente).
_MIN_COPARTICIPATION_SAMPLE = 5

# Raio-X da Receita — achado do Parecer Técnico "Boletim Insighta"
# (revisão 2, 14/09): _coparticipation_visibility_insight só dispara UMA
# vez (a "estreia" do dado); nada acompanhava a fatia de coparticipação
# depois disso. 5pp é o mesmo piso já usado em
# _OPME_CONCENTRATION_INCREASE_PP — mesmo "chute razoável" documentado
# no resto do arquivo.
_COPARTICIPATION_GROWTH_INCREASE_PP = 5.0

# "O que resta em aberto" da Auditoria de Templates e Insights: peça
# natural do mesmo padrão que Guia/coparticipação já fecharam —
# core.lotes.status/closed_at (Fase 2) já modelados, sem nenhum insight
# consumindo até esta rodada. Sem constante de limiar de dias AQUI, de
# propósito — diferente do resto deste arquivo, o corte "há quantos dias
# conta como parado" precisa chegar até a query SQL (mesmo motivo de
# APPEAL_DEADLINE_ALERT_HORIZON_DAYS viver em analytics_service.py, não
# aqui): quem decide isso é AnalyticsService._STALE_LOTE_AFTER_DAYS, que
# alimenta LoteRepository.stale_open_lotes_summary já filtrado — este
# motor só recebe a contagem pronta (ver _stale_open_lotes_insight
# abaixo) e decide "mostra ou não", nunca reaplica o corte.

# PMR (Prazo Médio de Recebimento) — achado da auditoria "Veredito do
# Gestor Clínico" (Seção 4, Achado 2): billing.created_at/settled_at
# sempre existiram no banco, na mesma linha, mas nenhum indicador do
# produto calculava essa diferença. Diferente do bloco de Lotes acima,
# o corte AQUI não precisa chegar até a query SQL (a agregação —
# AnalyticsRepository.payment_lag_total — devolve a média crua, sem
# filtro de "quando alertar"), então os limiares vivem no motor, mesmo
# padrão do resto deste arquivo.
#
# _PAYMENT_LAG_MARKET_BENCHMARK_DAYS é só contexto NARRATIVO (citado na
# mensagem quando o prazo da própria clínica já passa dele) — nunca o
# gatilho do alerta, que são os dois limiares abaixo.
#
# CORREÇÃO — Parecer Técnico "Boletim Insighta" (revisão 2, 14/09)
# sugeriu subir este número para 120 dias, citando "ANAHP 2024, quase o
# dobro de 2022". Verificado via busca antes de aplicar (indicadores do
# Sistema de Indicadores Hospitalares da Anahp, cobertos por Medicina
# S/A e Saúde Business): o PMR real do setor em 2024 foi de
# aproximadamente 69 dias — CAINDO frente aos ~76 dias de 2023, não
# subindo. A alegação de 120 dias não se sustenta contra a fonte
# primária; mantido aqui o número real verificado, não o do parecer.
# 60/90 (limiares de alerta abaixo) continuam um "chute razoável"
# ancorado nesse benchmark (mesma limitação de todo o resto deste
# arquivo — Achado 7 da Auditoria de Templates e Insights: não
# calibrado contra dado real de produção).
_PAYMENT_LAG_MARKET_BENCHMARK_DAYS = 69.0
_PAYMENT_LAG_WARNING_DAYS = 60.0
_PAYMENT_LAG_CRITICAL_DAYS = 90.0
_MIN_PAYMENT_LAG_SAMPLE = 5  # mesmo raciocínio de amostra mínima do resto do arquivo

# Raio-X da Receita, frente "Evitando perdas" — contrato vencendo sem
# renovação. O horizonte de quantos dias à frente a QUERY já olha
# (CONTRACT_EXPIRING_ALERT_HORIZON_DAYS) vive em analytics_service.py,
# mesmo motivo de APPEAL_DEADLINE_ALERT_HORIZON_DAYS viver lá — mas a
# ESCALADA de severidade dentro dessa janela (crítico vs. atenção) é só
# deste motor, mesmo padrão do resto do arquivo. "Chute razoável" de v1,
# não calibrado com dado real (mesma limitação de sempre).
_CONTRACT_EXPIRING_CRITICAL_DAYS = 7

# Raio-X da Receita, frente "Gestão eficiente" — concentração de receita
# em poucos convênios. Exige pelo menos 2 convênios distintos faturados
# no período (com 1 só, "100% de concentração" é a estrutura do negócio,
# não uma anomalia). 60%/80% são o mesmo "chute razoável" documentado no
# resto do arquivo.
_MIN_PLANS_FOR_CONCENTRATION = 2
_REVENUE_CONCENTRATION_WARNING_PCT = 60.0
_REVENUE_CONCENTRATION_CRITICAL_PCT = 80.0

# Raio-X da Receita, frente "Melhorias" — ROI de marketing trazido pro
# feed (antes só existia isolado no relatório semanal, ver
# ReportDataService/compute_roi_pct). Piso de gasto mínimo antes de
# alertar: um teste de campanha de poucas dezenas de reais com ROI
# negativo é ruído, não um padrão que mereça a atenção da diretoria.
_MIN_MARKETING_SPEND_FOR_INSIGHT = 200.0
_MARKETING_ROI_CRITICAL_RATIO = -0.50  # receita atribuída menor que metade do gasto

# Raio-X da Receita, frente "Prevendo movimentos" — sazonalidade de
# agenda (comparação ANO contra ano, não semana contra semana como
# _weekday_drop_insight). Amostra mínima no ano passado: uma clínica com
# menos de 1 ano de uso (ou um período do ano passado com poucochíssimo
# volume) tornaria qualquer variação percentual ruído, não um padrão
# sazonal real. 20%/35% são o mesmo "chute razoável" documentado no
# resto do arquivo.
_YOY_MIN_LAST_YEAR_SAMPLE = 10
_YOY_DROP_WARNING_PCT = 20.0
_YOY_DROP_CRITICAL_PCT = 35.0


def _comparative_phrase(ratio: float) -> str:
    """Traduz uma razão numérica (ex: 1.8x) numa comparação que qualquer
    pessoa entende de ouvido, sem precisar fazer conta — usado em frases
    como 'isso é quase o dobro da média da equipe'. Puramente decorativo
    (o número exato sempre continua na mesma frase, isto só dá cor)."""
    if ratio >= 1.8:
        return "quase o dobro"
    if ratio >= 1.4:
        return "bem mais"
    return "um pouco mais"


@dataclass
class DenialReasonCount:
    plan_name: str
    reason_code: str
    count: int
    # UUID (string) do convênio — entra nesta rodada só para o botão de
    # ação do insight linkar direto pra fila de faturamento JÁ FILTRADA
    # por este convênio (ver DECISÃO em _denial_spike_insights). Default
    # "" para não quebrar chamada antiga/teste que só monta pelo nome.
    plan_id: str = ""


@dataclass
class InsightsPeriodInput:
    """Tudo que o motor precisa para UM período (atual ou anterior) —
    monta a partir de AnalyticsRepository + ReportingRepository no
    AnalyticsService, sem o motor nunca ver a sessão de banco."""

    denial_reason_counts: list[DenialReasonCount]
    financial_hole_total: float  # Divergência de Cobrança (cobrado < contratado)
    total_value_saved: float
    avg_capacity_utilization: float | None  # 0.0 a 1.0
    high_risk_no_show_count: int
    # Divergência de Recebimento (pago pela operadora < contratado, só
    # billings já conciliados) — default 0.0 para não quebrar chamadas
    # antigas que ainda não passam esse dado (ver AnalyticsService).
    payment_gap_total: float = 0.0
    # Recursos de glosa (ver app/sql/008_denial_appeals.sql) com prazo
    # vencendo em breve — estado "AGORA", não do período (ver DECISÃO em
    # AnalyticsService._period_insights_input: só o período atual recebe
    # o valor real, o anterior fica 0 de propósito).
    appeals_due_soon_count: int = 0
    # Volume de agendamentos por dia da semana (0=domingo..6=sábado — ver
    # AnalyticsRepository.appointment_weekday_histogram) — alimenta
    # _weekday_drop_insight. Default {} para não quebrar chamadas/testes
    # existentes que ainda não passam esse dado.
    weekday_appointment_counts: dict[int, int] = field(default_factory=dict)
    # Taxa de falta por dia da semana — {weekday: (no_show_count, total_
    # resolvido)} — ver AnalyticsRepository.weekday_no_show_rate_breakdown
    # e _weekday_no_show_rate_insight. Só faz sentido em `current` (a
    # comparação é intra-período, contra a própria média do período, não
    # período a período), mas o default {} existe pelo mesmo motivo de
    # weekday_appointment_counts: não quebrar chamadas/testes existentes.
    weekday_no_show_counts: dict[int, tuple[int, int]] = field(default_factory=dict)
    # % do valor faturado no período com denial_risk_level medium/high
    # (ver AnalyticsRepository.denial_risk_value_breakdown), e o valor em
    # R$ correspondente — None quando não há faturamento no período (%
    # sobre base zero é indefinida, mesmo princípio de _delta_pct).
    denial_risk_pct: float | None = None
    denial_at_risk_value: float = 0.0
    # --- Meta de faturamento anual (terceiro exemplo do briefing) ---
    # Todos com default para não quebrar chamadas/testes existentes, e
    # todos só fazem sentido em `current` (não há "meta do período
    # anterior" — é um estado presente, mesmo raciocínio de
    # appeals_due_soon_count). None em qualquer um dos dois primeiros
    # campos = "não gerar o insight" (sem meta configurada, ou sem
    # contexto de data para calcular o ritmo esperado).
    annual_revenue_goal: float | None = None  # Tenant.annual_revenue_goal — NUNCA calculado, só o valor manual
    elapsed_year_fraction: float | None = None  # 0.0 a 1.0 — fração do ano calendário já decorrida (calculado pelo service, não pelo motor, para manter esta função pura/testável)
    ytd_billed_total: float = 0.0  # faturamento acumulado do ano até hoje
    inactive_patients_count: int = 0  # pacientes sem atendimento há mais de 1 ano — nutre a recomendação de recuperação de carteira
    # Radar de Profissional Fora do Padrão — [(professional_id, nome,
    # taxa_de_risco, total_faturamentos)], só profissionais com amostra
    # mínima (ver AnalyticsRepository.professional_denial_rates). O id
    # entra nesta rodada só para o botão de ação linkar direto pro
    # profissional exato em /professionals (ver DECISÃO em
    # _professional_outlier_insight) — nunca exibido como texto solto.
    # Default [] pelo mesmo motivo dos demais campos com default: não
    # quebrar chamadas/testes existentes que ainda não passam esse dado.
    professional_denial_rates: list[tuple[str, str, float, int]] = field(default_factory=list)
    # {dia_da_semana: contagem} de agendamentos FUTUROS com risco médio/
    # alto de falta (ver AnalyticsRepository.upcoming_risk_count_by_weekday)
    # — dá ao insight de taxa de falta por dia (_weekday_no_show_rate_insight)
    # um "e olha, você já tem N marcadas pra esse dia com risco", em vez
    # de só um padrão histórico sem conexão com o que já está na agenda.
    # Só faz sentido em `current` (é "agora", mesmo raciocínio de
    # appeals_due_soon_count) — default {} pelo motivo de sempre: não
    # quebrar chamada/teste que ainda não passa esse dado.
    upcoming_risk_count_by_weekday: dict[int, int] = field(default_factory=dict)
    # Taxa de ocupação por profissional no período atual — [(professional_id,
    # nome, taxa_de_ocupação 0-1)], só quem tem grade cadastrada (ver
    # DECISÃO em AnalyticsService.get_smart_insights; mesmo motivo de
    # professionals_without_availability_count no schema de agenda-metrics:
    # sem grade, "ocupação" não tem denominador). Mesmo raciocínio de
    # professional_denial_rates acima: o SERVIÇO só entrega o dado cru por
    # profissional, é o MOTOR (_capacity_drop_insight) que decide qual é
    # o pior caso — mantém a mesma divisão de responsabilidade em todo o
    # arquivo. Default [] pelo motivo de sempre.
    professional_utilization_rates: list[tuple[str, str, float]] = field(default_factory=list)
    # {canal_agendamento: (no_show_count, total_relevante)} — achado do
    # Dicionário de Dados (campo novo do Template de Agenda). Ver
    # AnalyticsRepository.booking_channel_no_show_rate_breakdown e
    # _booking_channel_no_show_insight. Default {} pelo motivo de
    # sempre: não quebrar chamada/teste que ainda não passa esse dado.
    booking_channel_no_show_counts: dict[str, tuple[int, int]] = field(default_factory=dict)
    # {motivo_cancelamento: count} + total de cancelamentos do período
    # (SEPARADO — nem todo cancelamento tem motivo preenchido, ver
    # DECISÃO em AnalyticsRepository.cancellation_reason_breakdown).
    # Default 0/{} pelo motivo de sempre.
    cancellation_reason_counts: dict[str, int] = field(default_factory=dict)
    total_cancelled_count: int = 0
    # Total faturado no período (ReportingRepository.billing_summary),
    # denominador do % de OPME abaixo — inclui billing SEM item_type
    # preenchido, de propósito (ver DECISÃO em
    # AnalyticsRepository.item_type_charged_value_breakdown). Default 0.0
    # pelo motivo de sempre: não quebrar chamada/teste existente.
    total_billed: float = 0.0
    # {item_type: soma de charged_value} — achado do Dicionário de Dados
    # (campo novo do Template de Faturamento). Só material_opme é lido
    # hoje (ver _opme_concentration_insight), mas o dict inteiro fica
    # disponível para insights futuros sobre os outros tipos.
    item_type_charged_value: dict[str, float] = field(default_factory=dict)
    # Coparticipação (parte paga pelo PACIENTE, distinta de charged_value
    # — ver AnalyticsRepository.coparticipation_summary): valor total,
    # contagem de billings com essa coluna preenchida, e total geral de
    # billings do período (denominador do "presente em Y% das contas").
    coparticipation_total: float = 0.0
    coparticipation_billing_count: int = 0
    total_billing_count: int = 0
    # Épico F4.2 do Plano Diretor ("Fechar lacunas operacionais") — de
    # toda coparticipação COBRADA (coparticipation_total acima), quanto
    # ainda não foi confirmada como recebida do paciente (ver
    # AnalyticsRepository.coparticipation_unconfirmed_summary). Estado
    # "AGORA" (como no_show_risk_score etc.): só o período atual recebe
    # o valor real, o anterior fica no default — não existe "pendência
    # de confirmação do período anterior" com sentido de negócio.
    coparticipation_unconfirmed_value: float = 0.0
    coparticipation_unconfirmed_count: int = 0
    # Lotes de faturamento (core.lotes) com status='aberto' há mais de
    # _STALE_LOTE_AFTER_DAYS dias, e a idade em dias do mais antigo deles
    # — ver AnalyticsService._period_insights_input e
    # LoteRepository.stale_open_lotes_summary. Estado "AGORA", mesmo
    # raciocínio de appeals_due_soon_count: só o período atual recebe o
    # valor real, o anterior fica no default (não existe "lotes abertos
    # do período anterior" — comparar contra si mesmo não faz sentido).
    stale_open_lotes_count: int = 0
    oldest_open_lote_age_days: int | None = None
    # PMR (Prazo Médio de Recebimento) — achado da auditoria "Veredito
    # do Gestor Clínico": billing.created_at/settled_at sempre
    # existiram no banco, mas nenhum indicador calculava essa diferença
    # até esta rodada (ver AnalyticsRepository.payment_lag_total e
    # _payment_lag_insight). Estado do PERÍODO (billing criado na
    # janela, já conciliado) — comparável contra o período anterior,
    # mesmo raciocínio de financial_hole_total/payment_gap_total, ao
    # contrário de appeals_due_soon_count/stale_open_lotes_count (que
    # são "AGORA"). None quando não há amostra (nenhum billing
    # conciliado no período).
    avg_days_to_receive: float | None = None
    payment_lag_settled_count: int = 0
    # Contratos de repasse vencendo sem renovação já cadastrada (Raio-X
    # da Receita, frente "Evitando perdas") — ver
    # ContractRepository.expiring_without_renewal_summary e
    # AnalyticsService.CONTRACT_EXPIRING_ALERT_HORIZON_DAYS. Estado
    # "AGORA" (mesmo raciocínio de appeals_due_soon_count/
    # stale_open_lotes_count): um contrato vencendo em 5 dias não fica
    # "menos urgente" por não ter mudado desde ontem — só o período
    # atual recebe o valor real.
    expiring_contracts_count: int = 0
    soonest_expiring_contract_plan_name: str | None = None
    soonest_expiring_contract_days: int | None = None
    # Concentração de receita em poucos convênios (Raio-X da Receita,
    # frente "Gestão eficiente") — {plan_name: valor faturado no
    # período}, já ordenado do maior para o menor pelo repositório (ver
    # AnalyticsRepository.revenue_by_plan). O motor
    # (_revenue_concentration_insight) decide sozinho o que conta como
    # "concentrado" a partir da distribuição bruta, não recebe um
    # percentual pré-calculado — mesma divisão de responsabilidade de
    # professional_denial_rates/professional_utilization_rates acima.
    revenue_by_plan: dict[str, float] = field(default_factory=dict)
    # ROI de marketing (Raio-X da Receita, frente "Melhorias") — dado já
    # existia isolado no relatório semanal (ReportDataService); esta
    # rodada só o traz pro feed de insights. Estado do PERÍODO
    # (gasto/receita atribuída da janela do dashboard), comparável
    # contra o período anterior — mesmo raciocínio de financial_hole/
    # payment_gap.
    marketing_spend_total: float = 0.0
    marketing_revenue_attributed: float = 0.0
    # Raio-X da Receita, frente "Evitando perdas" — billing já conciliado
    # com Divergência de Recebimento que ainda não tem recurso de glosa
    # aberto (ver AnalyticsRepository.payment_gap_without_appeal_summary
    # e _payment_gap_without_appeal_insight). Backlog "AGORA" (mesmo
    # raciocínio de appeals_due_soon_count/stale_open_lotes_count): só o
    # período atual recebe o valor real.
    payment_gap_without_appeal_count: int = 0
    payment_gap_without_appeal_value: float = 0.0
    # Raio-X da Receita, frente "Prevendo movimentos" — total de
    # agendamentos no MESMO período, um ano antes (ver
    # AnalyticsService._year_ago_period e _yoy_seasonality_insight). Só
    # existe em `current` (comparar "ano passado" do período ANTERIOR
    # não faz sentido — o insight já compara current contra isso). None
    # = não calculado (chamador antigo/teste que não passa esse dado).
    yoy_last_year_appointment_count: int | None = None
    # Raio-X da Receita, frente "Prevendo movimentos" — pacientes em
    # risco de abandono ANTECIPADO (ver AnalyticsRepository.
    # count_early_churn_risk_patients e _early_churn_insight). Estado
    # "AGORA" (mesmo raciocínio de appeals_due_soon_count): só o período
    # atual recebe o valor real.
    early_churn_risk_count: int = 0


@dataclass
class Insight:
    severity: str  # "critical" | "warning" | "positive" | "comparativo" (ver build_network_comparativo_insight)
    title: str
    message: str
    # DECISÃO — categoria de área ("faturamento" | "agenda"), pedido explícito
    # do usuário depois de ver o feed da Sala de Comando na prática: com
    # cobrança/glosa e agenda/ocupação misturadas na mesma lista, a tela
    # ficava "embolada" — o gestor do financeiro e o da recepção cuidam de
    # problemas diferentes, mas viam tudo junto. SmartInsightsFeed.tsx usa
    # isto pra agrupar em duas seções, mantendo só o insight de maior
    # impacto (a manchete) fora de qualquer seção. Sem default de propósito:
    # cada função de insight abaixo precisa declarar a sua categoria
    # explicitamente, nunca herdar uma categoria errada por omissão.
    category: str
    financial_impact: float | None = None  # em R$; usado só para ordenar por relevância
    # Marca insights de recursos lançados nesta rodada (Sala de Comando
    # 2.0) — nunca uma afirmação de "dado novo apareceu hoje" sobre o
    # ACHADO em si (isso já é o que o insight inteiro comunica), só do
    # TIPO de insight ser recente na plataforma. True hoje só em
    # _professional_outlier_insight e build_network_comparativo_insight.
    is_new: bool = False
    # Botão de ação real dentro do card (ver DECISÃO no topo do arquivo)
    # — texto do botão + destino, que o frontend interpreta em 3 formatos:
    # "/rota" (navega pra outra tela), "#tab:id" (troca de aba dentro da
    # própria Sala de Comando) ou "#id" (rola até aquele card na mesma
    # tela). Nenhum destino aqui é inventado: só aponta pra telas/seções
    # que já existem e já têm o dado que resolve o problema do insight.
    action_label: str | None = None
    action_href: str | None = None


def describe_denial_reason(code: str) -> str:
    """Tradução em português simples de um reason_code do motor de glosa
    (denial_risk_engine.py) — pública porque analytics_service.py também
    usa isto pra montar o `top_reason_label` do Comparativo (ver
    build_network_comparativo_insight)."""
    return _REASON_PLAIN.get(code, code)


def is_true_denial_risk_reason(code: str) -> bool:
    """DECISÃO — corrige um rótulo enganoso encontrado ao documentar o
    produto para o usuário: "value_below_contract_revenue_leak" é um dos
    reason_code que o motor de risco grava (ver denial_risk_engine.py::
    _rule_value_mismatch), mas o comentário do PRÓPRIO motor já deixa
    claro que cobrar ABAIXO do contrato não é risco de recusa — "convênio
    não recusa por cobrar barato demais", é vazamento de receita da
    própria clínica (categoria diferente, já coberta por
    _financial_hole_insight). Sem este filtro, tanto
    _denial_spike_insights (card "Convênio X está recusando mais
    pagamentos") quanto o "por onde começar" do Comparativo de glosa
    (ver AnalyticsService.get_smart_insights) podiam nomear esse motivo
    como causa de RECUSA — o oposto do que ele significa. Público porque
    os dois lugares precisam do mesmo filtro."""
    return code != "value_below_contract_revenue_leak"


def _index_reason_counts(counts: list[DenialReasonCount]) -> dict[tuple[str, str], int]:
    return {(c.plan_id, c.reason_code): c.count for c in counts}


def _high_risk_billing_href(plan_id: str) -> str:
    """DECISÃO — deep-link para a fila de faturamento JÁ FILTRADA pelo
    convênio que disparou o insight (antes: sempre "/", a fila GERAL —
    o usuário via "Unimed está recusando mais" e precisava procurar
    sozinho quais faturamentos eram da Unimed). `insurance_plan_id` é o
    parâmetro que o Painel usa de verdade (ver
    BillingRepository.list_high_risk_paginated e DashboardPage.tsx,
    frontend) — o `action_label` já nomeia o convênio na própria frase
    do botão, então o destino não precisa repetir o nome na URL."""
    return f"/?insurance_plan_id={plan_id}"


def _denial_spike_insights(current: InsightsPeriodInput, previous: InsightsPeriodInput) -> list[Insight]:
    """1 card por CONVÊNIO (nunca por combinação convênio+motivo — ver
    DECISÃO no topo do arquivo). Cada motivo que disparou (novo padrão OU
    salto de volume) entra na lista do convênio; o de maior volume atual
    vira a manchete da frase, os demais somam num "e mais N motivo(s)".

    Filtra fora "value_below_contract_revenue_leak" antes de tudo (ver
    DECISÃO em is_true_denial_risk_reason) — este card é especificamente
    sobre RECUSA de pagamento, e esse motivo não é recusa nenhuma."""
    current_counts = [c for c in current.denial_reason_counts if is_true_denial_risk_reason(c.reason_code)]
    previous_counts = [c for c in previous.denial_reason_counts if is_true_denial_risk_reason(c.reason_code)]
    current_idx = _index_reason_counts(current_counts)
    previous_idx = _index_reason_counts(previous_counts)
    plan_names = {c.plan_id: c.plan_name for c in current_counts}

    # plan_id -> lista de (reason_code, current_count, is_new_pattern, growth_pct)
    flagged_by_plan: dict[str, list[tuple[str, int, bool, float]]] = {}

    for (plan_id, reason_code), current_count in current_idx.items():
        previous_count = previous_idx.get((plan_id, reason_code), 0)

        if previous_count == 0:
            if current_count >= _MIN_SAMPLE_FOR_TREND:
                flagged_by_plan.setdefault(plan_id, []).append((reason_code, current_count, True, 0.0))
            continue

        if previous_count < _MIN_SAMPLE_FOR_TREND:
            continue  # amostra anterior baixa demais para "variação %" significar algo

        growth_pct = ((current_count - previous_count) / previous_count) * 100
        if growth_pct >= _SPIKE_THRESHOLD_PCT:
            flagged_by_plan.setdefault(plan_id, []).append((reason_code, current_count, False, growth_pct))

    insights: list[Insight] = []
    for plan_id, flags in flagged_by_plan.items():
        plan_name = plan_names[plan_id]
        flags.sort(key=lambda f: f[1], reverse=True)  # maior volume primeiro -> vira a manchete
        headline_reason, headline_count, headline_is_new, headline_growth = flags[0]
        total_cases = sum(f[1] for f in flags)
        other_count = len(flags) - 1

        headline_phrase = describe_denial_reason(headline_reason)
        if headline_is_new:
            what_happened = f"passou a recusar pagamento porque {headline_phrase}"
        else:
            what_happened = f"está recusando {headline_growth:.0f}% mais pagamentos do que antes, principalmente porque {headline_phrase}"

        others_note = (
            f" Também apareceu mais {other_count} motivo(s) diferente(s) de recusa nesse mesmo convênio."
            if other_count > 0
            else ""
        )

        insights.append(
            Insight(
                severity="critical",
                category="faturamento",
                title=f"{plan_name} está recusando mais pagamentos que o normal",
                message=(
                    f"Nos últimos dias, a {plan_name} {what_happened} — {total_cases} atendimento(s) afetado(s) "
                    f"nesta janela.{others_note} Antes de enviar a próxima cobrança pra essa operadora, vale revisar "
                    "esses atendimentos com calma, pra não cair na mesma recusa de novo."
                ),
                action_label=f"Ver faturamentos de alto risco da {plan_name}",
                action_href=_high_risk_billing_href(plan_id),
            )
        )
    return insights


def _financial_hole_insight(current: InsightsPeriodInput, previous: InsightsPeriodInput) -> Insight | None:
    if current.financial_hole_total <= 0:
        return None
    delta = current.financial_hole_total - previous.financial_hole_total
    trend = "e essa diferença está aumentando" if delta > 0 else "mas essa diferença já está estável ou diminuindo"
    return Insight(
        severity="warning",
        category="faturamento",
        title="Você está cobrando menos do que devia de alguns convênios",
        message=(
            f"Nos últimos dias, sua clínica cobrou R$ {current.financial_hole_total:,.2f} abaixo do que estava "
            f"combinado nos contratos — isso não é o convênio recusando nada, é a sua própria cobrança saindo "
            f"mais barata do que deveria, dinheiro que nem chegou a ser pedido, {trend}. Vale conferir se a "
            "tabela de preços de cada convênio está em dia no cadastro de Contratos."
        ),
        financial_impact=current.financial_hole_total,
        # DECISÃO — antes o botão só levava pro cadastro de Contratos
        # (achado do usuário: "vale conferir a tabela de preços" não
        # dizia QUAIS contas estavam erradas, só um valor total em R$).
        # Agora aponta pra lista real das contas — paciente, procedimento,
        # convênio, valor cobrado x valor contratado (ver DECISÃO em
        # AnalyticsRepository.list_financial_hole_billings e
        # FinancialHoleBillingsPanel.tsx, frontend) — a correção da
        # tabela de preços em si continua em Contratos, linkada de dentro
        # do próprio painel.
        action_label="Ver contas abaixo do combinado",
        action_href="#buraco-financeiro",
    )


def _payment_gap_insight(current: InsightsPeriodInput, previous: InsightsPeriodInput) -> Insight | None:
    if current.payment_gap_total <= 0:
        return None
    delta = current.payment_gap_total - previous.payment_gap_total
    trend = "e essa diferença está aumentando" if delta > 0 else "mas essa diferença já está estável ou diminuindo"
    return Insight(
        severity="critical",
        category="faturamento",
        title="Um convênio pagou menos do que devia por atendimentos já confirmados",
        message=(
            f"Você cobrou certo, mas o convênio pagou R$ {current.payment_gap_total:,.2f} menos do que o "
            f"combinado em contrato, em atendimentos que já foram confirmados e recebidos, {trend}. Diferente "
            "de uma recusa de pagamento, aqui a operadora aceitou a conta e pagou errado — você tem o direito "
            "de contestar esse valor."
        ),
        financial_impact=current.payment_gap_total,
        action_label="Abrir um recurso",
        action_href="/denial-appeals",
    )


def _payment_lag_insight(current: InsightsPeriodInput, previous: InsightsPeriodInput) -> Insight | None:
    """
    PMR (achado da auditoria "Veredito do Gestor Clínico" — Seção 4,
    Achado 2): o segundo maior vilão financeiro do setor ao lado da
    glosa — mesmo com o PMR médio real da ANAHP em queda (~69 dias em
    2024, vindo de ~76 em 2023, ver _PAYMENT_LAG_MARKET_BENCHMARK_DAYS),
    ainda é tempo de caixa preso que a clínica não recupera sozinha — e
    o dado pra calculá-lo
    (billing.created_at/settled_at) sempre esteve no banco sem nenhum
    indicador consumindo. Estado do PERÍODO (billing criado na janela,
    já conciliado) — comparável contra o período anterior, mesmo
    raciocínio de _financial_hole_insight/_payment_gap_insight, ao
    contrário de _appeals_due_soon_insight/_stale_open_lotes_insight
    (que são "AGORA").

    Sem financial_impact de propósito: estimar quanto dinheiro fica
    "preso" pelo atraso exigiria multiplicar dias por uma taxa de
    oportunidade de capital que este produto não tem como saber — mesmo
    princípio de nunca inventar um número que pareça mais preciso do
    que a informação disponível sustenta.
    """
    if current.avg_days_to_receive is None or current.payment_lag_settled_count < _MIN_PAYMENT_LAG_SAMPLE:
        return None
    if current.avg_days_to_receive < _PAYMENT_LAG_WARNING_DAYS:
        return None

    severity = "critical" if current.avg_days_to_receive >= _PAYMENT_LAG_CRITICAL_DAYS else "warning"

    trend_note = ""
    if previous.avg_days_to_receive is not None and previous.payment_lag_settled_count >= _MIN_PAYMENT_LAG_SAMPLE:
        delta_days = current.avg_days_to_receive - previous.avg_days_to_receive
        if delta_days >= 5:
            plural = "s" if round(delta_days) != 1 else ""
            trend_note = f" E está piorando: {delta_days:.0f} dia{plural} a mais do que no período anterior."

    market_note = ""
    if current.avg_days_to_receive > _PAYMENT_LAG_MARKET_BENCHMARK_DAYS:
        market_note = (
            f" Isso já passa da média do setor de saúde suplementar no Brasil "
            f"(~{_PAYMENT_LAG_MARKET_BENCHMARK_DAYS:.0f} dias, segundo a ANAHP)."
        )

    return Insight(
        severity=severity,
        category="faturamento",
        title="Os convênios estão demorando demais pra pagar",
        message=(
            f"As contas já conciliadas neste período levaram em média {current.avg_days_to_receive:.0f} dias "
            f"entre o faturamento e o recebimento.{market_note}{trend_note} Vale revisar quais convênios estão "
            "puxando essa média pra cima e cobrar prazo deles."
        ),
        action_label="Ver prazo por convênio",
        action_href="/",
    )


def _value_saved_insight(current: InsightsPeriodInput, previous: InsightsPeriodInput) -> Insight | None:
    if current.total_value_saved <= 0:
        return None
    delta = current.total_value_saved - previous.total_value_saved
    if delta <= 0:
        return None  # só celebra quando o número de fato melhorou
    return Insight(
        severity="positive",
        category="faturamento",
        title="O sistema evitou que você perdesse dinheiro com pagamento recusado",
        message=(
            f"Nos últimos dias, o Insighta corrigiu cobranças antes de elas serem enviadas e evitou "
            f"R$ {current.total_value_saved:,.2f} em possíveis recusas de pagamento — R$ {delta:,.2f} a mais "
            "do que no período anterior. Continue assim: quanto mais cedo o erro é corrigido, menos dinheiro "
            "fica perdido no caminho."
        ),
        financial_impact=current.total_value_saved,
    )


def _opme_concentration_insight(current: InsightsPeriodInput, previous: InsightsPeriodInput) -> Insight | None:
    """
    OPME (Órtese/Prótese/Material Especial — `item_type == "material_opme"`
    em app/models/billing.py, campo novo do Template de Faturamento,
    achado do Dicionário de Dados) é, na prática do setor de saúde
    suplementar, o tipo de item com maior escrutínio dos convênios: exige
    autorização prévia e nota fiscal do fornecedor anexada, e costuma
    concentrar os maiores valores glosados quando essa documentação falta.
    Este insight não inventa um score de risco por OPME (o motor de risco
    em denial_risk_engine.py não olha item_type) — só torna VISÍVEL uma
    concentração que antes não aparecia em nenhum relatório, com uma
    recomendação de checagem documental genérica e sempre válida para
    esse tipo de item.

    Achado 4 da Auditoria (médio) — em vez de um corte estático (que
    faria este card aparecer TODO carregamento do painel numa clínica de
    perfil ortopédico, virando ruído permanente), só alerta quando a
    concentração SOBE de forma material vs. o período anterior — mesmo
    raciocínio de "só destaca quando piora" já usado em
    _financial_hole_insight/_payment_gap_insight/_value_saved_insight. O
    piso (`_OPME_CONCENTRATION_MIN_PCT`) evita alertar sobre uma alta
    percentual em cima de uma fatia irrelevante do faturamento.
    """
    opme_value = current.item_type_charged_value.get("material_opme", 0.0)
    if opme_value <= 0 or current.total_billed <= 0:
        return None
    pct = (opme_value / current.total_billed) * 100
    if pct < _OPME_CONCENTRATION_MIN_PCT:
        return None
    previous_opme_value = previous.item_type_charged_value.get("material_opme", 0.0)
    previous_pct = (previous_opme_value / previous.total_billed) * 100 if previous.total_billed > 0 else 0.0
    if pct - previous_pct < _OPME_CONCENTRATION_INCREASE_PP:
        return None  # concentração estável (ou caindo) — perfil normal desta clínica, não uma mudança recente
    return Insight(
        severity="warning",
        category="faturamento",
        title="A fatia de material especial (OPME) no seu faturamento subiu",
        message=(
            f"R$ {opme_value:,.2f} ({pct:.0f}% do faturado no período) é órtese, prótese ou material especial "
            f"(OPME) — {pct - previous_pct:.0f} pontos percentuais acima do período anterior ({previous_pct:.0f}%). "
            "Esse tipo de item costuma exigir autorização prévia do convênio e nota fiscal do fornecedor "
            "anexada pra não ser recusado. Vale conferir se essa documentação está completa antes de enviar "
            "essas guias."
        ),
        financial_impact=opme_value,
        action_label="Ver faturamentos",
        action_href="/",
    )


def _coparticipation_visibility_insight(current: InsightsPeriodInput, previous: InsightsPeriodInput) -> Insight | None:
    """
    Coparticipação (`Billing.coparticipation_value`, campo novo do
    Template de Faturamento) é a parte que o PRÓPRIO PACIENTE paga,
    distinta do que o convênio cobre (`charged_value`) — achado do
    Raio-X da Sala de Comando: essa fatia de receita não aparecia em
    NENHUM relatório antes desta rodada. Diferente dos demais insights
    de Faturamento (que são alertas), este é "positive" — o objetivo é
    só confirmar pro cliente que o dado está chegando e já tem volume
    suficiente pra confiar nele, não sinalizar um problema.

    Achado 4 da Auditoria (médio) — sem gate contra o período anterior,
    este card apareceria PARA SEMPRE assim que a amostra mínima fosse
    cruzada uma vez, virando o mesmo ruído permanente do OPME acima. Sem
    adicionar estado persistido nenhum (não existe uma tabela de "o
    cliente já viu este aviso"), usa o próprio período anterior como
    proxy stateless de "primeira vez": só alerta quando o período
    ANTERIOR ainda não tinha amostra suficiente e o ATUAL passou a ter —
    ou seja, o momento em que o dado passou a ser confiável. Uma vez que
    os dois períodos consecutivos já cruzam a amostra mínima, o card para
    de aparecer sozinho (a condição de "primeira vez" deixa de valer).
    """
    if current.coparticipation_billing_count < _MIN_COPARTICIPATION_SAMPLE or current.coparticipation_total <= 0:
        return None
    if previous.coparticipation_billing_count >= _MIN_COPARTICIPATION_SAMPLE:
        return None  # período anterior já tinha amostra confiável — não é mais "a primeira vez", fica calado
    pct_of_billings = (
        (current.coparticipation_billing_count / current.total_billing_count) * 100
        if current.total_billing_count > 0
        else 0.0
    )
    return Insight(
        severity="positive",
        category="faturamento",
        title="Agora você também enxerga o quanto o paciente paga de coparticipação",
        message=(
            f"Neste período, R$ {current.coparticipation_total:,.2f} foram registrados como coparticipação — "
            f"a parte que o PACIENTE paga, separada do que o convênio cobre — presente em {pct_of_billings:.0f}% "
            "dos seus faturamentos. Esse valor não aparecia em nenhum relatório antes; vale conferir se está "
            "batendo com o que de fato foi cobrado do paciente na recepção."
        ),
        financial_impact=current.coparticipation_total,
    )


def _coparticipation_growth_insight(current: InsightsPeriodInput, previous: InsightsPeriodInput) -> Insight | None:
    """
    Achado do Parecer Técnico "Boletim Insighta" (revisão 2): o insight
    acima (_coparticipation_visibility_insight) é um "aviso de
    boas-vindas" que dispara UMA ÚNICA vez, no momento em que o dado
    passa a ser confiável — depois disso, nenhum insight acompanhava a
    fatia de coparticipação no faturamento de forma contínua. Este
    insight fecha essa lacuna com o MESMO padrão já usado em
    _opme_concentration_insight: só alerta quando a fatia SOBE de forma
    material vs. o período anterior. Exige amostra confiável NOS DOIS
    períodos (o inverso do insight de cima, que exige o contrário) —
    de propósito: um cobre a "estreia" do dado, este cobre a
    "tendência" depois dela, nunca os dois no mesmo carregamento.

    Sem inventar "inadimplência" de propósito: hoje o produto registra
    quanto foi COBRADO de coparticipação (Billing.coparticipation_value),
    nunca se o paciente de fato PAGOU — fabricar uma taxa de
    inadimplência sem esse dado seria exatamente o tipo de confiança que
    o motor inteiro se recusa a inventar. Este insight fica em "virou
    fatia maior da receita", não em "não foi pago".
    """
    if (
        current.coparticipation_billing_count < _MIN_COPARTICIPATION_SAMPLE
        or previous.coparticipation_billing_count < _MIN_COPARTICIPATION_SAMPLE
        or current.total_billed <= 0
        or previous.total_billed <= 0
    ):
        return None
    pct = (current.coparticipation_total / current.total_billed) * 100
    previous_pct = (previous.coparticipation_total / previous.total_billed) * 100
    if pct - previous_pct < _COPARTICIPATION_GROWTH_INCREASE_PP:
        return None
    return Insight(
        severity="warning",
        category="faturamento",
        title="A fatia de coparticipação no seu faturamento está subindo",
        message=(
            f"R$ {current.coparticipation_total:,.2f} ({pct:.0f}% do faturado no período) foram registrados como "
            f"coparticipação — a parte que o PACIENTE paga — {pct - previous_pct:.0f} pontos percentuais acima do "
            f"período anterior ({previous_pct:.0f}%). Vale confirmar que a recepção está de fato cobrando e "
            "recebendo esse valor do paciente na hora do atendimento, não só lançando no sistema."
        ),
        financial_impact=current.coparticipation_total,
    )


def _coparticipation_unconfirmed_insight(current: InsightsPeriodInput) -> Insight | None:
    """
    Épico F4.2 do Plano Diretor ("Fechar lacunas operacionais") — fecha
    a lacuna que os DOIS insights de coparticipação acima deixam
    explicitamente em aberto (ver docstring de
    _coparticipation_growth_insight: "nunca se o paciente de fato
    PAGOU"). Agora que existe confirmação de recebimento
    (Billing.coparticipation_received — ver
    043_coparticipation_confirmation.sql), este insight soma o que foi
    COBRADO mas ainda não confirmado como recebido (NULL) ou confirmado
    que NÃO foi recebido (FALSE) — um vazamento de receita real, não
    hipotético.

    Amostra mínima na CONTAGEM de linhas não confirmadas (mesmo
    raciocínio de _MIN_COPARTICIPATION_SAMPLE): 1-2 linhas esquecidas é
    ruído operacional do dia a dia, não um padrão que merece alerta.
    """
    if current.coparticipation_unconfirmed_count < _MIN_COPARTICIPATION_SAMPLE or current.coparticipation_unconfirmed_value <= 0:
        return None
    return Insight(
        severity="warning",
        category="faturamento",
        title="Tem coparticipação cobrada que ainda não foi confirmada como recebida",
        message=(
            f"R$ {current.coparticipation_unconfirmed_value:,.2f} em coparticipação (a parte que o PACIENTE paga) "
            f"foram cobrados em {current.coparticipation_unconfirmed_count} atendimento(s) neste período, mas "
            "ninguém confirmou no sistema se esse valor de fato entrou no caixa. Vale conferir com a recepção e "
            "confirmar cada um — cobrado no papel não é o mesmo que recebido de verdade."
        ),
        financial_impact=current.coparticipation_unconfirmed_value,
    )


def _capacity_drop_insight(
    current: InsightsPeriodInput, previous: InsightsPeriodInput, estimated_idle_capacity_revenue_lost: float
) -> Insight | None:
    if current.avg_capacity_utilization is None or previous.avg_capacity_utilization is None:
        return None
    drop_pp = (previous.avg_capacity_utilization - current.avg_capacity_utilization) * 100
    if drop_pp >= _UTILIZATION_DROP_ALERT_PP:
        impact_note = (
            f" — isso representa cerca de R$ {estimated_idle_capacity_revenue_lost:,.2f} que deixaram de "
            "entrar só por falta de gente marcada, sem nem contar as faltas"
            if estimated_idle_capacity_revenue_lost > 0
            else ""
        )
        # DECISÃO — nomeia QUEM está ocioso, quando dá (achado do usuário:
        # "algum profissional específico" não é uma ação, é uma pergunta
        # de volta pro gestor). Mesmo critério de "worst case" de
        # _professional_outlier_insight: pega o de MENOR ocupação entre
        # quem tem grade, só nomeia se estiver de fato abaixo do piso de
        # "agenda livre" (mesmo limiar de occupancyBarClass/occupancyNote
        # em ExecutiveAgendaSummary.tsx, pra o texto nunca contradizer o
        # painel de apoio) — sem ninguém nesse caso, cai no texto
        # genérico de sempre em vez de inventar um nome.
        idlest = min(current.professional_utilization_rates, key=lambda t: t[2], default=None)
        if idlest and idlest[2] < _IDLE_PROFESSIONAL_UTILIZATION_THRESHOLD:
            professional_id, professional_name, utilization_rate = idlest
            who_note = (
                f" — principalmente {professional_name}, com só {utilization_rate * 100:.0f}% da agenda ocupada"
            )
            action_label = f"Ver candidatos pra agenda de {professional_name}"
            action_href = f"#professional:{professional_id}"
        else:
            who_note = ""
            action_label = "Ver ocupação por profissional"
            action_href = "#agenda-resumo"
        return Insight(
            severity="warning",
            category="agenda",
            title="Sua agenda está com mais horários vazios do que o normal",
            message=(
                f"Nos últimos dias, a agenda da sua clínica ficou {drop_pp:.0f} pontos percentuais mais vazia "
                f"do que estava antes{impact_note}{who_note}. Vale tentar preencher esses horários com quem já "
                "foi paciente e ainda não tem retorno marcado."
            ),
            financial_impact=estimated_idle_capacity_revenue_lost or None,
            action_label=action_label,
            action_href=action_href,
        )
    return None


def _no_show_risk_insight(current: InsightsPeriodInput, estimated_revenue_at_risk: float) -> Insight | None:
    if current.high_risk_no_show_count < _HIGH_RISK_NO_SHOW_ALERT_THRESHOLD:
        return None
    plural = "s" if current.high_risk_no_show_count != 1 else ""
    return Insight(
        severity="warning",
        category="agenda",
        title="Tem gente com boa chance de não aparecer nos próximos dias",
        message=(
            f"O sistema encontrou {current.high_risk_no_show_count} consulta{plural} marcada{plural} com alta "
            f"chance de o paciente faltar — se isso realmente acontecer, dá pra perder cerca de "
            f"R$ {estimated_revenue_at_risk:,.2f}. A boa notícia é que dá pra agir antes: ligar ou mandar "
            "mensagem confirmando a presença costuma reduzir bastante esse risco."
        ),
        financial_impact=estimated_revenue_at_risk,
        action_label="Ver quem está em risco",
        action_href="#agenda-resumo",
    )


def _weekday_drop_insight(current: InsightsPeriodInput, previous: InsightsPeriodInput) -> Insight | None:
    """
    Traduz a comparação de agenda por dia da semana em texto acionável —
    exatamente o exemplo do briefing de redesenho: em vez de um número
    frio de ocupação média, aponta QUAL dia caiu e por QUANTO, para o
    gestor saber onde agir sem precisar cruzar números sozinho.

    Só compara dias que têm amostra mínima no período ANTERIOR
    (_MIN_WEEKDAY_SAMPLE) — sem isso, "1 consulta virou 0" seria
    tecnicamente "-100%" todo dia parado, ruído puro. Só reporta QUEDA
    (um salto de agenda não é um alerta, é uma boa notícia que já aparece
    como número em Agenda & Capacidade, sem precisar virar alerta textual).

    DECISÃO — só o PIOR dia vira card, não um por dia
    -------------------------------------------------------------------
    Achado real (mesmo espírito de _denial_spike_insights/
    _professional_outlier_insight): uma clínica com 3 dias da semana
    caindo ao mesmo tempo mostrava 3 cards quase idênticos no feed —
    "linguiça" pura. Agora só o dia com a MAIOR queda vira insight; os
    demais continuam visíveis como número no gráfico de apoio de Agenda
    & Capacidade, sem precisar de um card de texto cada.
    """
    candidates: list[tuple[int, int, int, float]] = []  # (weekday, previous_count, current_count, drop_pct)
    for weekday in range(7):
        previous_count = previous.weekday_appointment_counts.get(weekday, 0)
        if previous_count < _MIN_WEEKDAY_SAMPLE:
            continue
        current_count = current.weekday_appointment_counts.get(weekday, 0)
        drop_pct = ((previous_count - current_count) / previous_count) * 100
        if drop_pct < _WEEKDAY_DROP_WARNING_PCT:
            continue
        candidates.append((weekday, previous_count, current_count, drop_pct))

    if not candidates:
        return None

    weekday, previous_count, current_count, drop_pct = max(candidates, key=lambda c: c[3])
    severity = "critical" if drop_pct >= _WEEKDAY_DROP_CRITICAL_PCT else "warning"
    label = _WEEKDAY_LABELS[weekday]
    return Insight(
        severity=severity,
        category="agenda",
        title=f"{label.capitalize()} está com menos consultas marcadas",
        message=(
            f"Toda {label} sua clínica costumava ter {previous_count} consulta(s) marcada(s) — nas "
            f"últimas semanas, caiu para {current_count} (uma queda de {drop_pct:.0f}%). Quem costumava "
            f"vir numa {label} e ainda não tem retorno marcado é um bom primeiro grupo pra recontatar."
        ),
        # DECISÃO — antes o botão só levava pro gráfico de volume ("olha
        # que caiu"), sem terminar no passo que reverte a perda (achado
        # do usuário: "vale entender o motivo" não é uma ação). Agora
        # aponta pra lista real de quem recontatar — ver DECISÃO em
        # AnalyticsRepository.list_recall_candidates e
        # ExecutiveAgendaSummary.tsx (frontend), que também mostra o
        # gráfico de volume na mesma seção.
        action_label=f"Ver quem costumava vir {label}",
        action_href=f"#weekday:{weekday}",
    )


def _yoy_seasonality_insight(current: InsightsPeriodInput) -> Insight | None:
    """
    Raio-X da Receita, frente "Prevendo movimentos": `_weekday_drop_insight`
    já compara contra a SEMANA anterior, mas uma queda sazonal normal
    (ex: dezembro sempre esfria, ou uma especialidade sempre cai no
    início do ano) dispararia esse insight TODO ano na mesma época,
    mesmo sendo o padrão normal da própria clínica — ruído recorrente,
    não um alerta de verdade. Comparar contra o MESMO período do ANO
    passado responde uma pergunta diferente: "isso é pior do que era
    nessa mesma época, historicamente?" — se sim, é sinal de queda real
    (perda de pacientes, concorrência, problema pontual), não só o ciclo
    natural do negócio.

    Só alerta em QUEDA (mesmo raciocínio do resto do motor: crescimento
    não é alarme, já aparece como número positivo em qualquer relatório).
    """
    if (
        current.yoy_last_year_appointment_count is None
        or current.yoy_last_year_appointment_count < _YOY_MIN_LAST_YEAR_SAMPLE
    ):
        return None
    current_total = sum(current.weekday_appointment_counts.values())
    drop_pct = (
        (current.yoy_last_year_appointment_count - current_total) / current.yoy_last_year_appointment_count
    ) * 100
    if drop_pct < _YOY_DROP_WARNING_PCT:
        return None
    severity = "critical" if drop_pct >= _YOY_DROP_CRITICAL_PCT else "warning"
    return Insight(
        severity=severity,
        category="agenda",
        title="Sua agenda está bem mais fraca do que no mesmo período do ano passado",
        message=(
            f"Nesses mesmos dias, no ano passado, sua clínica teve {current.yoy_last_year_appointment_count} "
            f"consulta(s) marcada(s) — agora são {current_total}, uma queda de {drop_pct:.0f}%. Isso já é "
            "mais do que uma variação normal de semana a semana: vale entender se foi sazonalidade do seu "
            "setor, perda de pacientes pra concorrência, ou algo pontual (férias de um profissional, por "
            "exemplo)."
        ),
        action_label="Ver resumo de agenda",
        action_href="#agenda-resumo",
    )


def _early_churn_insight(current: InsightsPeriodInput) -> Insight | None:
    """
    Raio-X da Receita, frente "Prevendo movimentos": `_annual_goal_insight`
    já recomenda "reativar quem não voltou" citando pacientes INATIVOS
    (piso fixo de 1 ano) — um alerta tardio, depois que o paciente já foi
    embora de verdade. Este insight é o alarme ANTECIPADO: pacientes que
    já estão bem além do PRÓPRIO ritmo histórico de retorno, mas ainda
    não completaram 1 ano de ausência (ver AnalyticsRepository.
    count_early_churn_risk_patients) — quanto mais cedo a clínica liga,
    maior a chance de reverter antes que vire uma perda definitiva.

    Estado "AGORA" (mesmo raciocínio de appeals_due_soon_count): sempre
    calculado a partir de hoje, não escopado pelo período do dashboard.
    """
    if current.early_churn_risk_count <= 0:
        return None
    plural = "s" if current.early_churn_risk_count != 1 else ""
    return Insight(
        severity="warning",
        category="agenda",
        title="Tem paciente sumindo do próprio padrão, mesmo sem completar 1 ano fora",
        message=(
            f"{current.early_churn_risk_count} paciente{plural} já está bem além do próprio ritmo de retorno — "
            "comparado ao intervalo que cada um costuma esperar entre consultas, não a um prazo genérico — mas "
            "ainda não chegou a 1 ano de ausência. Ligar agora, enquanto o vínculo ainda está fresco, costuma "
            "funcionar melhor do que esperar completar 1 ano pra tentar recuperar."
        ),
        action_label="Ver quem está sumindo",
        action_href="#carteira-inativa",
    )


def _worst_no_show_weekday(current: InsightsPeriodInput) -> tuple[int, float, float] | None:
    """Achado compartilhado por _weekday_no_show_rate_insight e pelo
    "por onde começar" do Comparativo de taxa de falta (ver
    describe_worst_no_show_weekday abaixo) — mesmo piso de amostra
    mínima e mesmo critério "X pontos acima da MÉDIA do próprio
    período" dos dois lugares, calculado uma única vez. Retorna
    (weekday, taxa_do_dia, taxa_média_do_período) do dia com o MAIOR
    desvio acima da média, ou None se nenhum dia tem amostra suficiente
    ou nenhum está acima do piso de aviso."""
    total_no_show = sum(no_show for no_show, _ in current.weekday_no_show_counts.values())
    total_relevant = sum(total for _, total in current.weekday_no_show_counts.values())
    if total_relevant == 0:
        return None
    overall_rate = total_no_show / total_relevant

    candidates: list[tuple[int, float, float]] = []  # (weekday, rate, gap_pp)
    for weekday in range(7):
        no_show_count, total = current.weekday_no_show_counts.get(weekday, (0, 0))
        if total < _MIN_WEEKDAY_SAMPLE:
            continue
        rate = no_show_count / total
        gap_pp = (rate - overall_rate) * 100
        if gap_pp < _WEEKDAY_NO_SHOW_RATE_WARNING_PP:
            continue
        candidates.append((weekday, rate, gap_pp))

    if not candidates:
        return None

    weekday, rate, _gap_pp = max(candidates, key=lambda c: c[2])
    return weekday, rate, overall_rate


def describe_worst_no_show_weekday(current: InsightsPeriodInput) -> str | None:
    """Tradução em português simples do dia da semana com pior taxa de
    falta — pública porque analytics_service.py também usa isto pra
    montar o "por onde começar" do Comparativo de taxa de falta (ver
    build_network_comparativo_insight), mesmo espírito de
    describe_denial_reason para a métrica de glosa."""
    worst = _worst_no_show_weekday(current)
    if worst is None:
        return None
    weekday, _rate, _overall_rate = worst
    return _WEEKDAY_LABELS[weekday]


def _weekday_no_show_rate_insight(current: InsightsPeriodInput) -> Insight | None:
    """
    Responde diretamente "qual dia da semana tem taxa de falta alta" —
    achado do usuário sobre lacuna do módulo de Agenda (weekday_appointment_counts/
    _weekday_drop_insight só mostrava VOLUME, nunca a taxa). Compara
    cada dia contra a MÉDIA do próprio período (intra-período), não
    contra o período anterior nem contra um corte absoluto — o mesmo
    corte de 30% pode ser trivial pra uma clínica de estética e grave
    pra uma de saúde mental, então "X pontos ACIMA da sua própria média"
    generaliza melhor entre clínicas do que um número fixo.

    Só entram dias com amostra mínima (_MIN_WEEKDAY_SAMPLE) — mesmo
    raciocínio de _weekday_drop_insight: 1 falta em 1 atendimento seria
    "100%", ruído estatístico, não um padrão. Só reporta dias ACIMA da
    média (um dia ótimo não é um alerta, já aparece como número no
    gráfico de apoio de Agenda & Capacidade).

    DECISÃO — só o PIOR dia vira card (mesmo raciocínio de
    _weekday_drop_insight acima) — usa _worst_no_show_weekday, o mesmo
    cálculo que alimenta o "por onde começar" do Comparativo.
    """
    worst = _worst_no_show_weekday(current)
    if worst is None:
        return None
    weekday, rate, overall_rate = worst
    gap_pp = (rate - overall_rate) * 100
    severity = "critical" if gap_pp >= _WEEKDAY_NO_SHOW_RATE_CRITICAL_PP else "warning"
    label = _WEEKDAY_LABELS[weekday]
    comparison = _comparative_phrase(rate / overall_rate) if overall_rate > 0 else "bem mais"
    # DECISÃO — conecta o padrão HISTÓRICO (taxa de falta por dia) com o
    # que já está marcado pra frente (achado do usuário: "um lembrete
    # pode ajudar" não diz quem ligar nem quando) — quando já existem
    # consultas futuras nesse mesmo dia da semana com risco médio/alto
    # (ver AnalyticsRepository.upcoming_risk_count_by_weekday), o texto
    # aponta o número exato em vez de ficar só na generalidade histórica.
    upcoming_count = current.upcoming_risk_count_by_weekday.get(weekday, 0)
    upcoming_note = (
        f" Aliás, você já tem {upcoming_count} consulta(s) marcada(s) pra {label} que vem que também corre "
        "esse risco — vale confirmar essas primeiro."
        if upcoming_count > 0
        else ""
    )
    return Insight(
        severity=severity,
        category="agenda",
        title=f"As pessoas faltam mais nas {label}s do que nos outros dias",
        message=(
            f"Numa {label} comum, {rate * 100:.0f}% das consultas marcadas na sua clínica acabam sendo "
            f"falta — {comparison} da média dos outros dias ({overall_rate * 100:.0f}%). Um lembrete de "
            f"confirmação enviado com 1 dia de antecedência, especialmente pras {label}s, costuma "
            f"resolver boa parte disso.{upcoming_note}"
        ),
        # Aponta pra visão focada nesse dia da semana (ver DECISÃO em
        # _weekday_drop_insight acima e ExecutiveAgendaSummary.tsx,
        # frontend) — mesma seção de sempre ("#agenda-resumo"), agora
        # filtrada e com a lista de recontato pra esse dia específico.
        action_label="Ver risco de falta",
        action_href=f"#weekday:{weekday}",
    )


def _booking_channel_no_show_insight(current: InsightsPeriodInput) -> Insight | None:
    """
    Mesmo raciocínio de `_weekday_no_show_rate_insight` acima, mas por
    CANAL de agendamento (telefone, WhatsApp, site, presencial...) em vez
    de dia da semana — achado do Dicionário de Dados: `booking_channel` é
    campo novo do Template de Agenda, então esse insight só existe pra
    quem já começou a preencher essa coluna.

    Só o PIOR canal vira card (mesmo motivo de só reportar o pior dia em
    `_weekday_no_show_rate_insight`: evita empilhar um card quase
    idêntico por canal). Exige amostra mínima tanto no canal quanto no
    total geral — com poucos dados, um canal "ruim" pode ser só 1 falta
    em 2 agendamentos.
    """
    counts = current.booking_channel_no_show_counts
    total_no_show = sum(no_show for no_show, _ in counts.values())
    total_all = sum(total for _, total in counts.values())
    if total_all < _MIN_CHANNEL_NO_SHOW_SAMPLE or total_no_show == 0:
        return None
    overall_rate = total_no_show / total_all

    worst_channel: str | None = None
    worst_rate = 0.0
    for channel, (no_show, total) in counts.items():
        if total < _MIN_CHANNEL_NO_SHOW_SAMPLE:
            continue
        rate = no_show / total
        if rate > worst_rate:
            worst_rate = rate
            worst_channel = channel
    if worst_channel is None:
        return None

    gap_pp = (worst_rate - overall_rate) * 100
    if gap_pp < _CHANNEL_NO_SHOW_RATE_WARNING_PP:
        return None
    severity = "critical" if gap_pp >= _CHANNEL_NO_SHOW_RATE_CRITICAL_PP else "warning"
    comparison = _comparative_phrase(worst_rate / overall_rate) if overall_rate > 0 else "bem mais"
    return Insight(
        severity=severity,
        category="agenda",
        title=f"Quem agenda por {worst_channel} falta mais",
        message=(
            f"Consultas agendadas por {worst_channel} têm {worst_rate * 100:.0f}% de falta — {comparison} da "
            f"média geral da sua clínica ({overall_rate * 100:.0f}%). Vale reforçar a confirmação especialmente "
            f"pra quem agenda por esse canal, ou repensar como esse canal agenda a consulta."
        ),
        action_label="Ver risco de falta",
        action_href="#agenda-resumo",
    )


def _cancellation_reason_insight(current: InsightsPeriodInput) -> Insight | None:
    """
    Responde "por que as pessoas estão cancelando" em vez de só "quantas
    cancelaram" — achado do Dicionário de Dados: `cancellation_reason` é
    campo novo do Template de Agenda. Só dispara quando um ÚNICO motivo
    concentra boa parte dos cancelamentos do período (ver DECISÃO em
    AnalyticsRepository.cancellation_reason_breakdown sobre o
    denominador ser o TOTAL cancelado, não a soma dos motivos
    preenchidos) — um motivo variado (10 motivos diferentes, nenhum
    dominante) não é um padrão acionável, é operação normal de clínica.
    """
    if current.total_cancelled_count < _MIN_CANCELLATION_SAMPLE or not current.cancellation_reason_counts:
        return None

    top_reason, top_count = max(current.cancellation_reason_counts.items(), key=lambda kv: kv[1])
    pct = (top_count / current.total_cancelled_count) * 100
    if pct < _CANCELLATION_REASON_CONCENTRATION_WARNING_PCT:
        return None
    severity = "critical" if pct >= _CANCELLATION_REASON_CONCENTRATION_CRITICAL_PCT else "warning"
    return Insight(
        severity=severity,
        category="agenda",
        title=f'A maioria dos cancelamentos é pelo mesmo motivo: "{top_reason}"',
        message=(
            f"Das {current.total_cancelled_count} consulta(s) cancelada(s) no período, {top_count} "
            f"({pct:.0f}%) foram por \"{top_reason}\" — vale investigar se dá pra reduzir esse motivo "
            "específico (ex: ajustar horário, revisar um processo interno, ou treinar quem agenda pra "
            "evitar esse cenário)."
        ),
        action_label="Ver agenda",
        action_href="#agenda-resumo",
    )


def _denial_risk_pct_insight(
    current: InsightsPeriodInput,
    *,
    warning_threshold: float = _DENIAL_RISK_PCT_WARNING,
    critical_threshold: float = _DENIAL_RISK_PCT_CRITICAL,
) -> Insight | None:
    """
    Traduz o backlog de risco de glosa em uma frase de urgência
    financeira em vez de uma contagem seca — segundo exemplo do briefing
    de redesenho ("risco de até 50% de glosas nas contas atuais").
    Baseado em VALOR (R$), não em contagem de linhas: para a diretoria,
    "quanto dinheiro está em risco" é a pergunta real por trás do número.

    `warning_threshold`/`critical_threshold` (Épico F2.1 do Plano
    Diretor — "Calibração por especialidade/porte"): opcionais, default
    nos mesmos valores de sempre (_DENIAL_RISK_PCT_WARNING/_CRITICAL) —
    quem chama (AnalyticsService.get_smart_insights) resolve o valor
    configurado do tenant via resolve_denial_risk_thresholds e passa
    aqui; sem configuração, caem nos defaults. Mesmo padrão não-quebrador
    já usado em no_show_risk_engine.assess().
    """
    if current.denial_risk_pct is None or current.denial_risk_pct < warning_threshold:
        return None
    severity = "critical" if current.denial_risk_pct >= critical_threshold else "warning"
    return Insight(
        severity=severity,
        category="faturamento",
        title="Boa parte do que você faturou corre risco de ser recusada pelo convênio",
        message=(
            f"Das contas que você fechou nesses últimos dias, uma parte que soma R$ {current.denial_at_risk_value:,.2f} "
            f"tem risco médio ou alto de o convênio recusar o pagamento — isso é {current.denial_risk_pct:.0f}% de "
            "tudo que foi cobrado na janela. Antes de mandar essas cobranças pro convênio, vale revisar linha por "
            "linha com quem cuida do faturamento."
        ),
        financial_impact=current.denial_at_risk_value,
        action_label="Ver faturamentos de alto risco",
        action_href="/",
    )


def _annual_goal_insight(current: InsightsPeriodInput) -> Insight | None:
    """
    Terceiro exemplo do briefing de redesenho: em vez de um gráfico frio
    de meta, um diagnóstico em texto com recomendação concreta de ação
    (captar cliente novo / recuperar quem sumiu). Confirmado explicitamente
    pelo usuário: a meta é SEMPRE manual (Tenant.annual_revenue_goal,
    configurada em Minha Clínica) — este motor nunca a calcula sozinho,
    só compara o real com o que foi configurado.

    Compara o faturamento acumulado do ano com o RITMO ESPERADO até
    hoje (meta * fração do ano decorrida), não com a meta inteira — do
    contrário o insight disparia todo santo dia até 31 de dezembro,
    mesmo para uma clínica no ritmo certo.
    """
    if current.annual_revenue_goal is None or current.elapsed_year_fraction is None:
        return None  # sem meta configurada, ou sem contexto de data — não é possível calcular o ritmo esperado
    if current.elapsed_year_fraction <= 0 or current.annual_revenue_goal <= 0:
        return None

    expected_by_now = current.annual_revenue_goal * current.elapsed_year_fraction
    if expected_by_now <= 0 or current.ytd_billed_total >= expected_by_now:
        return None  # no ritmo ou à frente da meta — sem alerta

    behind_pct = ((expected_by_now - current.ytd_billed_total) / expected_by_now) * 100
    if behind_pct < _ANNUAL_GOAL_BEHIND_WARNING_PCT:
        return None  # diferença pequena, dentro do ruído natural de ritmo mês a mês

    severity = "critical" if behind_pct >= _ANNUAL_GOAL_BEHIND_CRITICAL_PCT else "warning"
    progress_pct = (current.ytd_billed_total / current.annual_revenue_goal) * 100
    recovery_note = (
        f" Aliás, {current.inactive_patients_count} paciente(s) não voltam há mais de um ano — é um bom primeiro "
        "grupo pra chamar de volta."
        if current.inactive_patients_count > 0
        else ""
    )
    return Insight(
        severity=severity,
        category="faturamento",
        title="No ritmo atual, a meta do ano não vai ser alcançada",
        message=(
            f"Sua clínica já faturou R$ {current.ytd_billed_total:,.2f} este ano — isso é {progress_pct:.0f}% da "
            f"meta de R$ {current.annual_revenue_goal:,.2f} que você definiu, mas fica abaixo do que já devíamos "
            "ter alcançado nesta época do ano. Vale pensar em trazer pacientes novos ou reativar quem já foi "
            f"cliente e não voltou.{recovery_note}"
        ),
        # DECISÃO — botão só quando há de fato quem chamar de volta
        # (antes: nenhum botão, o texto recomendava mas não linkava pra
        # lugar nenhum — não existia uma lista de quem são esses
        # pacientes até esta rodada). Ver DECISÃO completa em
        # AnalyticsRepository.list_inactive_patients e
        # InactivePatientsPanel.tsx (frontend). Sem lista pra mostrar,
        # sem botão — nunca um link pra uma seção vazia.
        action_label="Ver quem não voltou" if current.inactive_patients_count > 0 else None,
        action_href="#carteira-inativa" if current.inactive_patients_count > 0 else None,
        financial_impact=expected_by_now - current.ytd_billed_total,
    )


def _appeals_due_soon_insight(current: InsightsPeriodInput) -> Insight | None:
    """Diferente dos outros insights (que comparam atual vs. anterior),
    este é puramente um alerta de estado presente — um prazo de recurso
    vencendo não fica "menos urgente" por não ter mudado desde ontem.
    Sempre 'critical': ao contrário de um buraco financeiro (perda que já
    aconteceu), um prazo perdido é uma perda IRREVERSÍVEL e evitável."""
    if current.appeals_due_soon_count <= 0:
        return None
    plural = "s" if current.appeals_due_soon_count != 1 else ""
    return Insight(
        severity="critical",
        category="faturamento",
        title="Você está perto de perder o direito de contestar uma recusa de pagamento",
        message=(
            f"Tem {current.appeals_due_soon_count} contestação{plural} de recusa de pagamento com o prazo "
            "acabando nos próximos dias — ou já vencido — e ainda sem protocolo enviado. Se o prazo passar, "
            "normalmente você perde o direito de reclamar esse dinheiro de volta. Vale protocolar o quanto antes."
        ),
        action_label="Ver recursos em aberto",
        action_href="/denial-appeals",
    )


def _payment_gap_without_appeal_insight(current: InsightsPeriodInput) -> Insight | None:
    """
    Raio-X da Receita, frente "Evitando perdas": diferente de
    `_payment_gap_insight` (que soma o gap do PERÍODO, um alerta sobre o
    que aconteceu recentemente), este cobre o BACKLOG inteiro — billing
    conciliado há muito tempo, com Divergência de Recebimento, pro qual
    NINGUÉM ainda abriu recurso. Mesmo raciocínio de
    `_appeals_due_soon_insight` (estado "AGORA", sempre crítico): dinheiro
    que a clínica tem direito de reclamar e ainda não reclamou não fica
    "menos urgente" por ter saído da janela de 7 dias do dashboard — ao
    contrário de um prazo vencendo, aqui não há prazo legal correndo
    (o relógio só começa quando o recurso é de fato protocolado), mas
    cada dia sem abrir o recurso é um dia a mais até receber esse valor.
    """
    if current.payment_gap_without_appeal_count <= 0:
        return None
    plural = "s" if current.payment_gap_without_appeal_count != 1 else ""
    return Insight(
        severity="critical",
        category="faturamento",
        title="Tem dinheiro que o convênio pagou a menos e ninguém contestou ainda",
        message=(
            f"Tem {current.payment_gap_without_appeal_count} conta{plural} onde o convênio pagou menos do que "
            f"o combinado em contrato — R$ {current.payment_gap_without_appeal_value:,.2f} no total — e "
            "nenhuma delas tem um recurso de glosa aberto ainda. Diferente de uma recusa, aqui a operadora já "
            "aceitou a conta e pagou errado: você tem o direito de contestar, só falta abrir o recurso."
        ),
        financial_impact=current.payment_gap_without_appeal_value,
        action_label="Abrir um recurso",
        action_href="/denial-appeals",
    )


def _stale_open_lotes_insight(current: InsightsPeriodInput) -> Insight | None:
    """
    "O que resta em aberto" da Auditoria de Templates e Insights: peça
    natural do mesmo padrão que Guia/coparticipação já fecharam — dado
    real já modelado (core.lotes.status/closed_at, Fase 2), só faltava
    um insight consumindo.

    Mesmo raciocínio de _appeals_due_soon_insight (alerta de estado
    PRESENTE, não comparação com período anterior — um lote aberto há
    muito tempo não fica "menos preocupante" por não ter mudado desde
    ontem), mas 'warning', não 'critical': diferente de um recurso de
    glosa vencendo, não há prazo LEGAL correndo aqui — o risco é
    operacional (guias dentro do lote ficam paradas, sem virar fatura,
    atrasando o recebimento), não uma perda irreversível de direito.

    Achado do Parecer Técnico "Boletim Insighta" (revisão 2): agora que
    a tela de gestão de Lotes existe (LotesPage.tsx), o botão de ação
    aponta pra ela — antes ficava sem ação DE PROPÓSITO ("nunca inventa
    destino", ver DECISÃO na dataclass Insight acima) porque só existia
    o endpoint, sem nenhuma tela consumindo.
    """
    if current.stale_open_lotes_count <= 0:
        return None
    plural = "s" if current.stale_open_lotes_count != 1 else ""
    age_note = (
        f" O mais antigo está aberto há {current.oldest_open_lote_age_days} dias."
        if current.oldest_open_lote_age_days is not None
        else ""
    )
    return Insight(
        severity="warning",
        category="faturamento",
        title="Tem lote de faturamento aberto há muito tempo",
        message=(
            f"Tem {current.stale_open_lotes_count} lote{plural} de faturamento aberto há muito tempo sem "
            f"fechar.{age_note} As guias dentro desses lotes ficam paradas — não avançam para fatura enquanto "
            "o lote não é fechado."
        ),
        action_label="Ver lotes abertos",
        action_href="/lotes",
    )


def _contract_expiring_insight(current: InsightsPeriodInput) -> Insight | None:
    """
    Raio-X da Receita, frente "Evitando perdas": `Contract.valid_until`
    sempre existiu no banco, mas nenhum insight avisava ANTES do
    vencimento — só o painel de Utilização de Contrato mostrava a data,
    exigindo que alguém abrisse a tela e notasse sozinho. Sem uma tabela
    de preço em dia, toda cobrança feita depois do vencimento corre risco
    maior de recusa (o motor de glosa já sinaliza "no_contract_reference"
    quando não encontra NENHUM contrato — este insight é o alerta que
    chega ANTES disso acontecer).

    Estado "AGORA" (mesmo raciocínio de _appeals_due_soon_insight/
    _stale_open_lotes_insight): um contrato vencendo em 5 dias não fica
    "menos urgente" por não ter mudado desde ontem — não compara contra o
    período anterior.
    """
    if current.expiring_contracts_count <= 0:
        return None
    plural = "s" if current.expiring_contracts_count != 1 else ""
    is_critical = (
        current.soonest_expiring_contract_days is not None
        and current.soonest_expiring_contract_days <= _CONTRACT_EXPIRING_CRITICAL_DAYS
    )
    soonest_note = ""
    if current.soonest_expiring_contract_plan_name and current.soonest_expiring_contract_days is not None:
        days = current.soonest_expiring_contract_days
        when = "hoje" if days <= 0 else f"em {days} dia{'s' if days != 1 else ''}"
        soonest_note = f" O mais próximo é o da {current.soonest_expiring_contract_plan_name}, que vence {when}."
    return Insight(
        severity="critical" if is_critical else "warning",
        category="faturamento",
        title="Tem contrato de convênio vencendo sem renovação cadastrada",
        message=(
            f"Tem {current.expiring_contracts_count} contrato{plural} de repasse vencendo nos próximos dias, "
            f"sem um contrato novo já cadastrado pra substituir.{soonest_note} Vale confirmar a renovação com "
            "o convênio e atualizar a tabela de preços antes do vencimento, pra não correr risco de recusa por "
            "tabela desatualizada."
        ),
        action_label="Ver contratos",
        action_href="/contracts",
    )


def _revenue_concentration_insight(current: InsightsPeriodInput) -> Insight | None:
    """
    Raio-X da Receita, frente "Gestão eficiente": nenhum insight hoje
    nomeia o risco estratégico de depender de 1-2 convênios pra maior
    parte da receita — um reajuste, descredenciamento ou atraso de
    pagamento de UM parceiro derruba o caixa inteiro da clínica. O
    Ranking de Perda por Convênio e o Comparativo já olham para convênio
    individualmente, mas nenhum dos dois soma "quanto % da receita total
    isso representa".

    Exige pelo menos `_MIN_PLANS_FOR_CONCENTRATION` convênios distintos
    faturados no período — com 1 só convênio, a concentração é 100% por
    definição (clínica fechada com uma única operadora, uma decisão de
    negócio, não uma anomalia a alertar) e o card seria ruído permanente.
    """
    if current.total_billed <= 0 or len(current.revenue_by_plan) < _MIN_PLANS_FOR_CONCENTRATION:
        return None
    top_plan, top_value = max(current.revenue_by_plan.items(), key=lambda kv: kv[1])
    pct = (top_value / current.total_billed) * 100
    if pct < _REVENUE_CONCENTRATION_WARNING_PCT:
        return None
    severity = "critical" if pct >= _REVENUE_CONCENTRATION_CRITICAL_PCT else "warning"
    return Insight(
        severity=severity,
        category="faturamento",
        title=f"Boa parte da sua receita depende de um único convênio: {top_plan}",
        message=(
            f"Nos últimos dias, {pct:.0f}% de tudo que sua clínica faturou veio de um único convênio, "
            f"{top_plan} (R$ {top_value:,.2f} de R$ {current.total_billed:,.2f} faturados). Se esse convênio "
            "atrasar um pagamento, reajustar mal ou descredenciar a clínica, o caixa inteiro sente — vale "
            "diversificar ativamente a carteira de convênios (e de pacientes particulares) pra reduzir essa "
            "dependência."
        ),
        financial_impact=top_value,
        action_label="Ver ranking por convênio",
        action_href="#tab:comparativo",
    )


def _marketing_roi_insight(current: InsightsPeriodInput, previous: InsightsPeriodInput) -> Insight | None:
    """
    Raio-X da Receita, frente "Melhorias": o cálculo de ROI de marketing
    (gasto de campanha × receita de paciente atribuído) já existia, mas
    isolado no relatório semanal por WhatsApp (ver ReportDataService) —
    fora do feed da Sala de Comando, só quem abrisse o PDF via essa
    conta. Mesmo cálculo simplificado documentado em
    ReportingRepository.revenue_from_campaign_patients (atribuição por
    janela, não por coorte rigorosa) — este insight não inventa uma
    métrica nova, só reexpõe a mesma com alerta quando o número fica
    ruim.

    Exige um piso mínimo de gasto (`_MIN_MARKETING_SPEND_FOR_INSIGHT`)
    antes de alertar — um gasto de poucas dezenas de reais com ROI
    negativo é ruído de teste de campanha, não um padrão que mereça
    atenção da diretoria.
    """
    if current.marketing_spend_total < _MIN_MARKETING_SPEND_FOR_INSIGHT:
        return None
    # compute_roi_pct devolve uma RAZÃO (-0.5 = -50%), apesar do nome —
    # mesma convenção já usada em ReportDataService/report_pdf_builder,
    # que só multiplica por 100 na hora de formatar (_fmt_pct). Seguimos
    # a mesma convenção aqui pra não inventar uma segunda unidade pro
    # mesmo cálculo.
    roi_ratio = compute_roi_pct(current.marketing_spend_total, current.marketing_revenue_attributed)
    if roi_ratio is None or roi_ratio >= 0:
        return None
    severity = "critical" if roi_ratio <= _MARKETING_ROI_CRITICAL_RATIO else "warning"
    trend_note = ""
    if previous.marketing_spend_total >= _MIN_MARKETING_SPEND_FOR_INSIGHT:
        previous_roi_ratio = compute_roi_pct(previous.marketing_spend_total, previous.marketing_revenue_attributed)
        if previous_roi_ratio is not None and roi_ratio < previous_roi_ratio - 0.10:
            trend_note = " E está piorando em relação ao período anterior."
    return Insight(
        severity=severity,
        category="faturamento",
        title="O marketing está gastando mais do que está trazendo de volta",
        message=(
            f"Nos últimos dias, sua clínica gastou R$ {current.marketing_spend_total:,.2f} em campanhas e a "
            f"receita atribuída a pacientes vindos delas foi R$ {current.marketing_revenue_attributed:,.2f} "
            f"— um ROI de {roi_ratio * 100:.0f}%.{trend_note} Vale revisar quais campanhas estão puxando esse "
            "número pra baixo antes de continuar investindo do mesmo jeito."
        ),
        financial_impact=current.marketing_spend_total - current.marketing_revenue_attributed,
    )


# Radar de Profissional Fora do Padrão — limiares de v1, mesmo espírito
# de "chute razoável" documentado em no_show_risk_engine.py: exige a
# taxa do profissional ser pelo menos o DOBRO da média da própria
# clínica E pelo menos 5 pontos percentuais acima em termos absolutos
# (evita marcar como "outlier" uma diferença de 2% vs 1%, que é 2x em
# proporção mas irrelevante em prática).
_PROFESSIONAL_OUTLIER_MIN_RATIO = 2.0
_PROFESSIONAL_OUTLIER_MIN_ABS_GAP = 0.05


def _professional_outlier_insight(current: InsightsPeriodInput) -> Insight | None:
    """Compara a taxa de risco de glosa de cada profissional (só quem
    tem amostra mínima — ver AnalyticsRepository.professional_denial_rates)
    contra a média da PRÓPRIA clínica no mesmo período — nunca contra
    outra clínica (isso é Nível 1/rede, não este motor, que é só dado
    local). Só o pior caso vira insight — evita empilhar um card por
    profissional numa clínica com vários acima da média."""
    if current.denial_risk_pct is None or not current.professional_denial_rates:
        return None
    tenant_avg = current.denial_risk_pct / 100  # denial_risk_pct já vem 0-100 (ver _denial_risk_pct)
    if tenant_avg <= 0:
        return None

    worst_id, worst_name, worst_rate, worst_total = max(current.professional_denial_rates, key=lambda t: t[2])
    if worst_rate < tenant_avg * _PROFESSIONAL_OUTLIER_MIN_RATIO:
        return None
    if worst_rate - tenant_avg < _PROFESSIONAL_OUTLIER_MIN_ABS_GAP:
        return None

    ratio = worst_rate / tenant_avg
    comparison = _comparative_phrase(ratio)
    return Insight(
        severity="warning",
        category="faturamento",
        title=f"{worst_name} está fora do padrão de glosa da equipe",
        message=(
            f"Dos atendimentos de {worst_name} nesses últimos dias, {worst_rate * 100:.0f}% correm risco de o "
            f"convênio recusar o pagamento ({worst_total} atendimento(s)) — {comparison} que a média da sua "
            f"clínica ({tenant_avg * 100:.0f}%). Isso costuma acontecer quando falta preencher o código da "
            f"doença (CID) ou do procedimento na hora do atendimento. Vale conversar com {worst_name} sobre "
            "esse preenchimento."
        ),
        is_new=True,
        action_label=f"Ver {worst_name} em Profissionais",
        # DECISÃO — deep-link direto para o profissional exato (antes:
        # sempre "/professionals", a lista GERAL — o usuário precisava
        # procurar sozinho pelo nome). `highlight` é lido por
        # ProfessionalsPage.tsx (frontend) para rolar e realçar a linha
        # exata, nunca para filtrar/escondar os demais profissionais.
        action_href=f"/professionals?highlight={worst_id}",
    )


# Comparativo entre clínicas como MANCHETE do feed — Sala de Comando 2.0,
# Nível 1 do roadmap ("só existe em escala"). Mesmo dado da aba
# Comparativo (rede/network_benchmark_service.py); aqui só decide quando
# o desvio é grande o bastante para virar destaque em texto, não só uma
# barra na aba dedicada. Abaixo de 3pp é a variação normal entre clínicas
# parecidas — não é "notícia".
_COMPARATIVO_MIN_GAP_PP = 3.0


def build_network_comparativo_insight(
    *,
    metric_label: str,
    category: str,
    your_rate: float,
    network_median: float,
    total_billed: float,
    top_reason_label: str | None = None,
    top_weekday_label: str | None = None,
) -> Insight | None:
    """Constrói o insight de Comparativo a partir do MESMO dado da aba
    Comparativo (your_rate/network_median já vêm com cohort suficiente —
    ver DECISÃO em app/sql/032_network_benchmark.sql: o SQL nunca devolve
    mediana sem amostra mínima, então esta função não precisa checar isso
    de novo). `financial_impact` é uma PROJEÇÃO (gap de taxa x faturamento
    do próprio período) — mesma natureza de aproximação de
    estimated_revenue_at_risk/estimated_idle_capacity_revenue_lost, nunca
    um número contábil fechado. Retorna None (nunca 0 ou um card vazio)
    quando total_billed é zero — sem faturamento no período, a projeção em
    R$ não tem base para existir.

    `top_reason_label` (opcional) — o motivo de glosa mais comum da
    própria clínica no período, já traduzido em português simples (ver
    describe_denial_reason). `top_weekday_label` (opcional) — o dia da
    semana com pior taxa de falta da própria clínica (ver
    describe_worst_no_show_weekday). O chamador passa só UM dos dois,
    de acordo com qual métrica é ("denial" recebe reason, "no_show"
    recebe weekday — não faz sentido misturar) — os dois dão ao "onde
    você está perdendo" um "por onde começar", em vez de só mostrar o
    gap em R$ sem pista de causa.

    `category` — recebido explícito do chamador (nunca inferido de qual
    dos dois labels acima veio preenchido: describe_denial_reason/
    describe_worst_no_show_weekday podem voltar None mesmo quando a
    métrica É a certa, ex: sem dado de motivo de glosa ainda — inferir
    pela presença do label classificaria errado nesse caso)."""
    gap_pp = (your_rate - network_median) * 100
    if gap_pp < _COMPARATIVO_MIN_GAP_PP:
        return None
    if total_billed <= 0:
        return None
    financial_impact = (your_rate - network_median) * total_billed
    metric_lower = metric_label[0].lower() + metric_label[1:] if metric_label else metric_label
    if top_reason_label:
        starting_point_note = (
            f" Na sua clínica, o motivo mais comum de recusa tem sido que {top_reason_label} — é um bom lugar "
            "pra começar a corrigir."
        )
    elif top_weekday_label:
        starting_point_note = (
            f" Na sua clínica, {top_weekday_label} costuma ser o dia com mais falta — é um bom lugar pra "
            "começar a agir."
        )
    else:
        starting_point_note = ""
    return Insight(
        severity="comparativo",
        category=category,
        title=f"Sua {metric_lower} está acima da rede",
        message=(
            f"Comparamos sua clínica com outras de porte parecido que também usam o Insighta (sempre em grupo, "
            f"nunca o dado de uma clínica específica): a sua {metric_lower} é {your_rate * 100:.1f}%, enquanto a "
            f"mediana dessas clínicas é {network_median * 100:.1f}% — {gap_pp:.1f} pontos abaixo da sua."
            f"{starting_point_note} Se você chegasse nesse nível, deixaria de perder cerca do valor abaixo todo mês."
        ),
        financial_impact=financial_impact,
        is_new=True,
        action_label="Ver comparativo completo",
        action_href="#tab:comparativo",
    )


def generate_insights(
    current: InsightsPeriodInput,
    previous: InsightsPeriodInput,
    estimated_no_show_revenue_at_risk: float = 0.0,
    estimated_idle_capacity_revenue_lost: float = 0.0,
    extra_insights: list[Insight] | None = None,
    denial_risk_warning_threshold: float = _DENIAL_RISK_PCT_WARNING,
    denial_risk_critical_threshold: float = _DENIAL_RISK_PCT_CRITICAL,
) -> list[Insight]:
    insights: list[Insight] = []
    insights.extend(_denial_spike_insights(current, previous))

    for maybe_insight in (
        _weekday_drop_insight(current, previous),
        _yoy_seasonality_insight(current),
        _early_churn_insight(current),
        _weekday_no_show_rate_insight(current),
        _appeals_due_soon_insight(current),
        _payment_gap_without_appeal_insight(current),
        _stale_open_lotes_insight(current),
        _contract_expiring_insight(current),
        _denial_risk_pct_insight(
            current, warning_threshold=denial_risk_warning_threshold, critical_threshold=denial_risk_critical_threshold
        ),
        _annual_goal_insight(current),
        _financial_hole_insight(current, previous),
        _payment_gap_insight(current, previous),
        _payment_lag_insight(current, previous),
        _value_saved_insight(current, previous),
        _capacity_drop_insight(current, previous, estimated_idle_capacity_revenue_lost),
        _no_show_risk_insight(current, estimated_no_show_revenue_at_risk),
        _professional_outlier_insight(current),
        _booking_channel_no_show_insight(current),
        _cancellation_reason_insight(current),
        _opme_concentration_insight(current, previous),
        _coparticipation_visibility_insight(current, previous),
        _coparticipation_growth_insight(current, previous),
        _coparticipation_unconfirmed_insight(current),
        _revenue_concentration_insight(current),
        _marketing_roi_insight(current, previous),
    ):
        if maybe_insight is not None:
            insights.append(maybe_insight)

    # Comparativo entre clínicas (ver build_network_comparativo_insight) —
    # vem de fora (analytics_service.get_smart_insights) porque exige uma
    # sessão de banco SEM tenant (Comparativo é cross-tenant), que este
    # motor puro nunca vê. Só o pior desvio entra — mesmo critério do
    # Radar de Profissional acima (só o pior caso vira manchete).
    if extra_insights:
        insights.extend(extra_insights)

    # Prioriza por impacto financeiro (maior primeiro); alertas sem valor
    # monetário associado (ex: queda de ocupação) ficam depois, ordenados
    # por severidade — crítico antes de comparativo antes de atenção antes
    # de positivo.
    severity_rank = {"critical": 0, "comparativo": 1, "warning": 2, "positive": 3}
    insights.sort(key=lambda i: (i.financial_impact is None, -(i.financial_impact or 0), severity_rank.get(i.severity, 99)))
    return insights
