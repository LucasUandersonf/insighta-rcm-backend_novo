# Tratamento de Dados Pessoais — Insighta

**Status: RASCUNHO TÉCNICO.** Este documento foi gerado a partir de uma leitura
verificada do código-fonte (branch `main`, 14/09/2026) — cada afirmação abaixo
sobre "o que o sistema faz" foi conferida linha a linha, não inventada. **Não é**
um documento jurídico pronto para due diligence: os campos marcados
`[DECISÃO PENDENTE — jurídico/negócio]` exigem uma decisão da clínica/empresa
que a engenharia não tem autoridade para tomar sozinha (prazo de retenção,
canal formal de atendimento ao titular, base legal final escolhida quando há
mais de uma aplicável). Trate como o esqueleto factual que um advogado
especializado em LGPD precisa revisar e completar — não como o documento final.

Ver DECISÃO de reposicionamento de produto no topo do `README.md` para o
contexto de por que isto passou a existir nesta revisão (Plano Diretor
Insighta, épico F4.1 — "LGPD e segurança").

---

## 1. Que dado pessoal o sistema processa

O Insighta é uma camada analítica que opera **sobre** dados já consolidados
vindos do ERP/sistema de prontuário do cliente (upload estruturado ou
webhook — ver "Reposicionamento de produto" no README). Ele nunca é o
sistema primário de prontuário eletrônico.

| Categoria | Campos | Onde vive |
|---|---|---|
| Identificação do paciente | Nome completo, CPF, cartão do convênio | `core.patients`, `core.billing.member_card_number` |
| **Dado de saúde (sensível, Art. 5º II da LGPD)** | Código CID-10 do atendimento | `core.appointments.cid_code`, `core.billing` (via join) |
| Identificação do profissional de saúde | Nome, registro profissional (CRM/CRO/etc) | `core.professionals` |
| Dado financeiro do atendimento | Valor cobrado/pago, procedimento (código TUSS) | `core.billing` |
| Credenciais de acesso | E-mail, senha (hash bcrypt/argon2, nunca texto puro), papel (RBAC) | `core.users` |
| Contato de destinatário de relatório | Nome, telefone WhatsApp (membro da equipe, não paciente) | `core.report_recipients` |

## 2. Base legal (LGPD, Art. 7º e Art. 11º)

O Insighta processa este dado **como operador**, a pedido da clínica
(controladora), no contexto de faturamento junto a operadoras de saúde —
atividade que só existe porque a clínica já atende o paciente sob um
contrato/relação de cuidado à saúde.

- Dado de identificação/financeiro: Art. 7º, V (execução de contrato) ou
  IX (legítimo interesse), a depender do contrato entre a clínica e o
  Insighta — **[DECISÃO PENDENTE — jurídico]**: qual das duas é a base
  formal adotada no contrato de prestação de serviço com cada clínica
  cliente.
- CID (dado de saúde, sensível): Art. 11º, II, `f` (tutela da saúde, em
  procedimento realizado por profissionais de saúde) é a base mais
  comumente aplicável a este tipo de processamento — **[DECISÃO
  PENDENTE — jurídico]**: confirmação formal com o corpo jurídico da
  clínica, já que é ela quem tem a relação direta com o paciente
  (controladora) e escolhe a base sob a qual coletou o dado
  originalmente.

## 3. Para onde o dado sai da infraestrutura própria (fluxos a terceiros)

Cada linha abaixo foi confirmada no código (ver referência), não é uma lista
genérica.

| Destino | O que sai | Contém CID/dado de saúde? | Quando |
|---|---|---|---|
| API da Anthropic (`api.anthropic.com`) — extração de contrato | Texto do PDF da tabela de preços do convênio | **Não** — só tabela de procedimento/preço, sem paciente | `app/services/contract_extraction_service.py` |
| API da Anthropic (`api.anthropic.com`) — rascunho de recurso de glosa | Motivo da negativa, tipo de guia, **código CID**, código de procedimento (nunca nome/CPF do paciente — ver `build_draft_user_message`) | **Sim** — código CID vai no prompt | `app/services/denial_appeal_draft_service.py`, sob demanda do usuário (botão "Gerar rascunho com IA"), auditado (ver seção 5) |
| Meta WhatsApp Business Platform | PDF do relatório semanal / lista de atendimentos de risco de falta (pode conter nome de paciente) | Pode conter nome, não CID | `app/services/whatsapp_client.py`, `weekly_report_job.py`/`daily_alert_job.py` — destinatário é um número da PRÓPRIA equipe da clínica (`core.report_recipients`), nunca o paciente |
| AWS S3 (3 buckets: contratos, anexos de recurso de glosa, arquivos de ingestão) | PDF de contrato (sem paciente), anexos de recurso (podem conter documento com dado de paciente), arquivo bruto de ingestão | Possível, depende do que o cliente anexa | `contract_storage_client.py`, `appeal_storage_client.py`, `ingestion_storage_client.py` — mesma conta AWS do provedor de infraestrutura, não um terceiro adicional |
| Sentry (opcional, só se `SENTRY_DSN` configurado) | Stack trace de erro | `send_default_pii=False` em todo lugar que inicializa Sentry — configurado para NUNCA enviar PII automaticamente | Todos os jobs e `app/main.py` |

**Achado desta revisão**: o único fluxo que envia dado de saúde (CID) para
um processador fora da infraestrutura própria é o rascunho de recurso de
glosa via IA. Isso está documentado tecnicamente desde a criação da
feature (ver `denial_appeal_draft_service.py`), mas nunca tinha sido
listado num documento de conformidade até agora — **é o achado central
deste épico**.

## 4. Onde o isolamento entre clientes (multi-tenancy) é garantido

- **Row-Level Security (RLS) no Postgres**, não só na aplicação — cada
  tabela com dado de tenant tem `FORCE ROW LEVEL SECURITY` (ver
  `app/sql/001_init_schema.sql`). Mesmo um bug de aplicação não vaza dado
  entre clínicas, porque o banco recusa a query, não a aplicação.
- RBAC de 5 papéis (owner/admin/financeiro/atendimento/auditor) —
  `atendimento` (recepção) nunca vê dado financeiro agregado nem os
  Dashboards de Decisão (`analytics.py`); `auditor` tem acesso de leitura
  amplo, incluindo a própria trilha de auditoria (seção 5), mas nunca
  escreve nada.
- Comparativo entre clínicas (Sala de Comando, aba Comparativo) **nunca**
  expõe dado de uma clínica específica para outra — só mediana/agregado
  de coorte, com amostra mínima (ver `app/sql/032_network_benchmark.sql`).

## 5. Trilha de auditoria (quem acessou/exportou, quando)

`core.audit_log` (RLS ativado, como qualquer tabela de tenant) registra
`(tenant_id, actor_user_id, action, entity_type, entity_id, created_at)`
para:

- Criação/atualização de paciente, faturamento, usuário, recurso de glosa
  (ações `*.created`/`*.updated`/`*.resolved`, já existentes antes desta
  revisão).
- **Novo nesta revisão (épico F4.1)**: download do documento de recurso de
  glosa em PDF (`document_downloaded` — contém CPF e CID) e geração do
  rascunho de justificativa via IA (`ai_draft_generated` — envia CID à
  Anthropic). Ver `DenialAppealService.build_appeal_document`/
  `draft_justification`.

O log nunca guarda o dado sensível em si (nunca CPF/CID/nome dentro do
próprio registro de auditoria) — só QUE a ação aconteceu, POR QUEM, QUANDO.
Guardar o dado sensível de novo dentro do log aumentaria a superfície de
exposição, o oposto do que a LGPD pede. Consultável via
`GET /api/v1/audit-log` (papéis owner/admin/auditor).

**Limitação conhecida, não escondida**: a trilha cobre ESCRITA e os dois
fluxos de EXPORTAÇÃO/PROCESSAMENTO POR TERCEIRO acima — não cobre toda
LEITURA de tela (ex: abrir a lista de faturamentos de alto risco não gera
uma linha de auditoria). Cobertura completa de leitura exigiria
instrumentar every GET que retorna dado de paciente, o que hoje não existe
e não foi escopo desta revisão — **[DECISÃO PENDENTE — produto]**: se vale
o custo de performance/armazenamento de logar toda leitura, ou se o
padrão atual (escrita + os pontos de maior risco de exportação) é
suficiente pra postura de conformidade da empresa.

## 6. Retenção de dado

**[DECISÃO PENDENTE — jurídico/negócio]**: não existe hoje nenhuma rotina
de expurgo automático de dado antigo (paciente inativo, tenant cancelado,
log de auditoria antigo). Isso não é um "não sei" técnico — é uma decisão
que depende de política da empresa (prazo contratual com o cliente, prazo
legal aplicável ao tipo de dado — prontuário médico tem prazo mínimo legal
de guarda no Brasil, mas o Insighta não é o prontuário primário) que a
engenharia não deve inventar. Até essa decisão existir, o dado permanece
armazenado indefinidamente enquanto o tenant estiver ativo.

## 7. Direitos do titular (acesso, correção, exclusão, portabilidade)

**[DECISÃO PENDENTE — produto/jurídico]**: não existe hoje um canal
formal dentro do produto para um paciente solicitar acesso/correção/
exclusão do próprio dado — porque o Insighta não tem relação direta com
o paciente (a clínica, controladora, é quem atende esse pedido). O que
existe tecnicamente hoje: como o dado do paciente é excluído/atualizado
na origem (ERP do cliente) e re-sincronizado via ingestão, uma correção
feita na origem se reflete no próximo upload/webhook. Formalizar o
processo (quem na clínica é o ponto de contato, prazo de resposta) é
decisão de produto/jurídico, não algo a inventar aqui.

---

*Gerado a partir do inventário técnico do motor de insights e da camada de
dados (branch `main`, 14/09/2026) — Plano Diretor Insighta, épico F4.1.
Revisão jurídica obrigatória antes de uso em due diligence ou resposta
formal a um titular de dados.*
