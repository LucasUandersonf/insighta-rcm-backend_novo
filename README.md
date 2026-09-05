# InsightaRCM — Camada Analítica e de Inteligência Financeira (Backend/FastAPI)

> **Sprint de Central de Upload e Redesign Petrol (29/08/2026, tarde):**
> até esta versão, o ÚNICO jeito de um lote operacional (CSV/XML/JSON)
> entrar no sistema era via S3 + SQS + `app/worker/ingestion_worker.py`
> — um caminho de infraestrutura, sem nenhuma porta HTTP; o cliente não
> tinha como subir um arquivo pela própria UI do produto. Isso foi
> corrigido: `POST /api/v1/ingestion/upload` (multipart) roda o MESMO
> pipeline de parsing/normalização do worker (extraído para
> `app/services/ingestion_processing_service.py`, usado pelos dois
> caminhos — nenhuma lógica duplicada), de forma síncrona — o usuário
> recebe o resultado (linhas importadas/rejeitadas) na mesma requisição.
> `GET /api/v1/ingestion/files` alimenta um histórico paginado. No
> frontend, nova tela "Central de Upload" (`/upload`) com duas abas
> (lotes operacionais + contratos de convênio em PDF) e componente de
> arrastar-e-soltar. Também: paleta visual migrada de grafite neutro
> para navy/petrol profundo (ver `frontend/tailwind.config.ts`), e as
> telas de Pacientes/Profissionais foram retiradas da navegação e das
> rotas (reposicionamento de produto — ver nota abaixo; os endpoints e
> os arquivos das páginas continuam existindo, só não são mais
> navegáveis, para não quebrar o fluxo de Consultas que ainda os usa
> internamente).

> **Sprint de Estabilização e Consolidação (29/08/2026):** a produção
> apresentava "Algo deu errado" na maioria das telas — causa raiz e
> correção documentadas na seção "Deploy em produção" abaixo. Além da
> correção, esta rodada entrega três frentes pedidas para consolidar o
> produto: (1) **Gestão de Contatos para Relatórios**
> (`app/api/v1/endpoints/report_recipients.py` + `app/sql/009_report_recipients.sql`)
> — múltiplos destinatários por tenant para os disparos automatizados,
> substituindo o destino único fixo do `weekly_report_job.py`; (2)
> **Logs de Auditoria** expostos via API
> (`app/api/v1/endpoints/audit_log.py`, sobre a tabela `core.audit_log`
> que já existia mas não tinha endpoint) e **paginação robusta**
> (`app/schemas/pagination.py` — envelope `{items, total, limit, offset}`)
> aplicada a `contracts/active`, `denial-appeals`, `patients` e
> `billing/high-risk`; (3) o design system do frontend foi elevado para
> um acabamento premium (paleta com token `accent`, tipografia serifada
> para headers, sombras suaves, skeleton loaders, estados de erro com
> "Tentar novamente") e os KPIs passaram a vir acompanhados de texto
> explicativo gerado automaticamente (`src/lib/narrative.ts`) — ver
> `frontend/README` (ou o histórico de commits do frontend) para o
> detalhe visual.

> **Reposicionamento de produto (28/08/2026):** este sistema não é um ERP
> clínico operacional. Ele é uma camada analítica/RCM que opera SOBRE os
> dados consolidados que já chegam de ERPs legados dos clientes (upload
> estruturado + webhooks nativos via `app/api/v1/endpoints/integrations.py`).
> As entidades `patients`/`appointments`/`professionals` continuam
> existindo como o schema canônico de destino da ingestão (Etapa 1/2 do
> pipeline) e os endpoints de escrita direta servem como ferramenta de
> correção manual pós-ingestão para a recepção, não como o fluxo primário
> do produto. Módulos novos desta fase — Gestão de Usuários (RBAC),
> Painel do Tenant/Plano e Central de Integrações & Webhooks — ver
> `app/sql/006_platform_admin.sql` e os endpoints `users`, `tenant`,
> `integrations`.

## Por que esta estrutura de pastas (arquitetura em camadas)

```
app/
├── main.py              # bootstrap: FastAPI app, middlewares globais, router
├── core/                # config e segurança — não depende de mais nada no projeto
│   ├── config.py        # Settings via env (pydantic-settings)
│   └── security.py      # hash de senha, JWT
├── db/                  # infraestrutura de acesso a dados
│   ├── base.py           # Declarative Base do SQLAlchemy
│   └── session.py        # engine assíncrono + a dependency get_db_with_tenant
│                          # (o "middleware de tenant" real — ver comentários no arquivo)
├── api/
│   ├── deps.py            # encadeamento JWT -> CurrentUser -> DbSession tenant-aware
│   └── v1/
│       ├── router.py       # agrega todos os sub-routers
│       └── endpoints/      # 1 arquivo por domínio (auth, billing, patients...)
├── models/               # ORM (SQLAlchemy) — espelha o schema core.* do Postgres
├── schemas/              # Pydantic — contratos de entrada/saída da API (anti mass-assignment)
├── repositories/         # SÓ queries. Não sabe de regra de negócio.
├── services/             # regra de negócio (motor de glosa, cálculo de ROI etc.)
└── sql/                  # scripts SQL que não são migrations "normais" do Alembic
    └── 002_auth_resolver.sql   # função SECURITY DEFINER para o problema do login sob RLS
```

### Por que separar `repositories/` de `services/`
Um repositório só sabe "buscar/gravar linhas". Um service sabe "o que
significa faturar uma consulta com alto risco de glosa". Separar os dois
permite testar a regra de negócio (services) com um repositório fake/mock,
sem precisar de um Postgres real rodando — os testes de regra de negócio
ficam rápidos e não dependem de infraestrutura.

### Por que `schemas/` (Pydantic) é uma pasta própria, separada de `models/` (ORM)
Nunca expomos o SQLAlchemy model diretamente na API. Isso:
1. Impede **mass assignment** — o cliente só pode enviar os campos que o
   schema de entrada declara (`tenant_id` e `role`, por exemplo, nunca
   aparecem nos schemas de entrada — só chegam via JWT).
2. Desacopla o contrato público da API da estrutura interna do banco —
   podemos renomear uma coluna no banco sem quebrar o contrato da API,
   e vice-versa.

### O "middleware de tenant" na prática
Não existe um arquivo `middleware/tenant.py` com `@app.middleware("http")`
neste projeto — de propósito. O motivo técnico está documentado em
`app/db/session.py`: o `SET LOCAL app.current_tenant` só tem efeito se
rodar na MESMA transação/conexão que as queries seguintes do mesmo
request, e isso só é garantido através da cadeia de `Depends()` do
FastAPI (`get_current_user` → `get_db`), não de um middleware ASGI
genérico. Ver `app/api/deps.py` para o encadeamento completo.

### O problema do login sob RLS
Login acontece ANTES de sabermos o tenant do usuário — mas a tabela
`core.users` tem RLS, então nenhuma linha é visível sem tenant setado.
Resolvido com uma função SQL `SECURITY DEFINER` (`core.resolve_login`,
em `app/sql/002_auth_resolver.sql`) que é o único ponto do sistema
autorizado a olhar credenciais cross-tenant, de forma restrita e
auditável — em vez de dar `BYPASSRLS` à role de runtime inteira.

## Rodando localmente
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # preencher DATABASE_URL, JWT_SECRET_KEY etc.
uvicorn app.main:app --reload
```

## Endpoints implementados
`auth` (login, cadastro público/self-signup, recuperação de senha —
ver seção própria abaixo), `users` (Gestão de Usuários/RBAC + troca de senha),
`tenant` (dados cadastrais + plano), `integrations` (chaves de API),
`billing`, `patients`, `appointments`, `contracts`, `insurance-companies`
(operadoras + planos), `denial-appeals` (Recurso de Glosa),
`analytics` (Dashboards de Decisão) — todos
seguindo o mesmo padrão em camadas: `endpoint -> service -> repository`,
com `DbSession` (tenant-aware) injetada via `app/api/deps.py` e RBAC via
`require_role(...)`. Note as diferenças de `_CAN_WRITE` por domínio:
`contracts`/`insurance-companies`/`denial-appeals`/`analytics` (dado
financeiro) excluem a role `atendimento`, que por sua vez pode escrever
livremente em `patients`/`appointments` (rotina de recepção).

## Cadastro público (self-signup) + recuperação de senha

Modelo SaaS: `POST /auth/register` cria a clínica (`core.tenants`) + o
primeiro usuário (sempre `role="owner"`, nunca aceito do payload) numa
única chamada, e já devolve o JWT — sem etapa de verificação de e-mail
nesta primeira versão. O plano escolhido (`plan_tier`) é só registrado
(`Tenant.is_active=True` desde já); a cobrança de verdade (gateway de
pagamento) é uma etapa futura, combinada explicitamente com o usuário —
hoje não existe nenhum estado de "assinatura pendente" no schema.

Duas sessões de banco cruzam essa única requisição
(`app/services/auth_service.py::AuthService.register`): `core.tenants`
não tem RLS, `core.users` tem — o serviço grava o tenant numa sessão
"crua" (`get_db_no_tenant`) e, só depois de ter o `tenant_id`, abre uma
segunda sessão tenant-aware (`get_db_with_tenant`) para gravar o owner.
Mesmo "ovo e galinha" que o login resolve com `core.resolve_login`
(ver seção abaixo), resolvido aqui em duas fases de escrita em vez de
uma função `SECURITY DEFINER`.

Recuperação de senha (self-service, `POST /auth/password-reset/request`
+ `POST /auth/password-reset/confirm`) segue a mesma lógica de
`core.resolve_login`: uma segunda função `SECURITY DEFINER`,
`core.resolve_user_by_email` (`app/sql/012_password_reset.sql`), localiza
o(s) usuário(s) daquele e-mail cross-tenant (o mesmo e-mail pode existir
em mais de uma clínica — consultor multi-clínica, mesmo cenário do
achado F-04 do login) sem nunca expor hash de senha. Um token de uso
único (`core.password_reset_tokens`, sem RLS, só o HASH SHA-256 do token
é persistido) é gerado por conta ativa encontrada, com validade curta
(`PASSWORD_RESET_TOKEN_EXPIRE_MINUTES`, padrão 30 min). `/request` SEMPRE
devolve `202` sem corpo, exista ou não o e-mail — mesmo princípio
anti-enumeração do login.

**Envio de e-mail semi-configurado, de propósito**: o fluxo inteiro
(geração de token, expiração, confirmação) já funciona de ponta a ponta
sem nenhum provedor de e-mail configurado — `app/services/email_client.py`
degrada graciosamente (mesmo padrão de `SENTRY_DSN`/`ANTHROPIC_API_KEY`
ausentes): sem `SMTP_HOST`/`SMTP_USERNAME`/`SMTP_PASSWORD` no ambiente,
o e-mail que seria enviado só é registrado em log estruturado (dá para
testar o fluxo completo em desenvolvimento sem depender de conta
externa nenhuma). Compatível com qualquer provedor que ofereça
credenciais SMTP (SendGrid, Mailgun, AWS SES, Postmark, Gmail/Workspace
etc.) — trocar de provedor é só preencher as variáveis de ambiente,
nunca mudar código. Ver `FRONTEND_BASE_URL` (usada para montar o link
`/reset-password?token=...` dentro do e-mail) e `SMTP_*` em
`app/core/config.py`.

### Login/cadastro com Google ("Sign in with Google")

`POST /auth/google` — usado tanto para login quanto para cadastro,
distinguindo por 3 estados na mesma resposta (`GoogleAuthResponse`, ver
`app/schemas/token.py`): login direto (`access_token`), ambiguidade
multi-tenant (`requires_tenant_selection` — mesmo mecanismo do login
tradicional) ou nenhuma conta com aquele e-mail (`needs_registration` +
`email`/`suggested_owner_name`, para o frontend pré-preencher o cadastro
sem pedir para digitar nome/e-mail de novo). `POST /auth/register`
aceita um `google_credential` no lugar de `owner_name`/`email`/`password`
para completar esse cadastro (CNPJ e plano continuam obrigatórios — o
Google só resolve identidade, nunca os dados da clínica).

**Sem client_secret nenhum**: o frontend usa o Google Identity Services
(`https://accounts.google.com/gsi/client`, botão renderizado pelo
próprio Google) e recebe um ID token JÁ ASSINADO no callback — este
backend só VERIFICA esse token (`app/services/google_oauth_client.py`,
via JWKS + `python-jose`, dependência que o projeto já tinha para os
próprios JWTs — sem puxar o SDK `google-auth`), conferindo assinatura,
emissor, expiração, a claim `aud` contra `GOOGLE_OAUTH_CLIENT_ID` e
`email_verified=true`. Sem `GOOGLE_OAUTH_CLIENT_ID` configurado,
`POST /auth/google` devolve `503` — mesma degradação graciosa do e-mail
transacional acima. Para ativar: criar um "OAuth client ID" do tipo
"Web application" no Google Cloud Console (APIs & Services >
Credentials), com as origens do frontend em "Authorized JavaScript
origins", e configurar o mesmo Client ID nos dois lados
(`GOOGLE_OAUTH_CLIENT_ID` aqui, `VITE_GOOGLE_OAUTH_CLIENT_ID` no
frontend) — é um valor PÚBLICO, não um segredo.

## Recurso de Glosa (conformidade ANS) — a segunda metade do ciclo de glosa

`denial_risk_engine.py` (já existia) cobre a glosa TÉCNICA — erro de
preenchimento detectado ANTES do envio à operadora, puro cálculo, sem
"processo" nenhum. `app/sql/008_denial_appeals.sql` cobre a outra
metade: a NEGATIVA FORMAL que a operadora devolve DEPOIS de receber a
guia (glosa administrativa/documental ou negativa médica de cobertura),
que abre um expediente de contestação com prazo — por isso é uma
entidade própria (`core.denial_appeals`), referenciando `billing`, não
um campo a mais nele.

Máquina de estados: `aberto` (negativa registrada) → `protocolado`
(recurso enviado à operadora, via `POST /denial-appeals/{id}/file`) →
`deferido` / `indeferido` / `nip_aberta` (via
`POST /denial-appeals/{id}/resolve`) — um `indeferido` pode ainda ser
escalado para `nip_aberta` (Notificação de Intermediação Preliminar na
ANS) quando o caso envolve direito do beneficiário; a decisão de
escalar é humana, o sistema só registra o estado. Anexos (comprovante de
protocolo, documentação exigida pela operadora) sobem via
`POST /denial-appeals/{id}/attachments` para um bucket S3 próprio
(`AWS_S3_APPEALS_BUCKET`, separado dos de ingestão e de contratos).

**Importante sobre o prazo**: diferente de reembolso ao beneficiário
(onde a ANS regula prazo), o prazo para a CLÍNICA contestar uma glosa é
CONTRATUAL — definido no contrato entre clínica e operadora, varia por
operadora. `insurance_companies.default_appeal_deadline_days` é
configurável por tenant (via `PATCH /insurance-companies/{id}`) a
partir do contrato real; `settings.DEFAULT_APPEAL_DEADLINE_DAYS` (30)
é só um fallback genérico para o campo nunca ficar em branco enquanto o
tenant não confirmou o número exato — o app nunca afirma isso como "a
lei diz". `app/services/appeal_deadline_calculator.py` é a função pura
que decide qual dos dois números usar (testada em
`tests/test_appeal_deadline_calculator.py`).

Um recurso com prazo vencendo nos próximos dias (ou já vencido) gera
`appeals_due_soon_count` em `GET /analytics/executive-summary` e um
insight `critical` dedicado em `GET /analytics/smart-insights` — ao
contrário de um buraco financeiro (perda que já aconteceu), um prazo
perdido é uma perda evitável, daí a severidade máxima.

## Parser Inteligente de Contratos (IA) — Convênio → Plano → Contrato → Itens

Hierarquia relacional (ver `app/sql/007_contract_intelligence.sql`):
`insurance_companies` (operadoras) → `insurance_plans` (agora ligado a
uma operadora) → `contracts` (cabeçalho: vigência, PDF original em S3,
status de homologação) → `contract_items` (a tabela de preços granular
em si: código TUSS + descrição + valor acordado por linha).

Por que quebrar o antigo `contracts` (uma linha = um procedimento) em
cabeçalho + itens: um contrato real tem UMA vigência/PDF e CENTENAS de
códigos TUSS — e só o cabeçalho tem onde guardar o resultado da extração
por IA (`status`, `extracted_at`, `homologated_by/at`).

Três passos, três endpoints (`app/api/v1/endpoints/contracts.py`):
1. `POST /contracts/upload` (multipart) — sobe o PDF pro S3
   (`AWS_S3_CONTRACTS_BUCKET`, bucket separado do de ingestão em lote) e
   cria o contrato com `status=rascunho`.
2. `POST /contracts/{id}/extract` — extrai texto do PDF (`pypdf`) e chama
   a IA (Anthropic Messages API, `app/services/contract_extraction_service.py`)
   pedindo JSON estrito `[{tuss_code, procedure_name, agreed_price}, ...]`.
   Devolve um PREVIEW — **nada é persistido em `contract_items` aqui** —
   e marca `status=em_revisao`.
3. `POST /contracts/{id}/homologate` — recebe a lista final revisada pelo
   faturista (a Tela de Conferência, human-in-the-loop) e só ENTÃO grava
   via `ContractItemRepository.replace_items`, marcando `status=homologado`.
   Só contratos homologados alimentam `denial_risk_engine`/analytics.

Cadastro manual (`POST /contracts`, sem PDF) continua existindo para 1-2
procedimentos digitados direto — cria já homologado.

### Os dois lados do "buraco financeiro" (`app/repositories/analytics_repository.py`)
- **Divergência de Cobrança** (`financial_hole`): quanto a CLÍNICA cobrou
  abaixo do `agreed_price` do item de contrato vigente.
- **Divergência de Recebimento** (`payment_gap`): quanto a OPERADORA
  efetivamente pagou (`billing.received_value`, preenchido só depois de
  `POST /billing/{id}/settle`) abaixo do `agreed_price` — só considera
  billings já conciliados. Ambas somadas por convênio/plano no período,
  expostas em `GET /analytics/executive-summary` e no Painel de Insights.

## Alembic — migrations
O schema inicial (`app/sql/001_init_schema.sql` + `002_auth_resolver.sql`)
foi escrito como SQL bruto, de propósito — RLS, `FORCE ROW LEVEL SECURITY`
e a função `SECURITY DEFINER` são DDL sensível demais para confiar no
autogenerate do Alembic, que não entende esses objetos. A partir desse
baseline, toda evolução de schema (novas colunas, novos índices, novas
tabelas "normais") passa a ser gerenciada por Alembic normalmente.

**Em desenvolvimento local**, rode manualmente:
```bash
alembic init  # já feito neste skeleton — não repetir
psql "$DATABASE_URL" -f app/sql/001_init_schema.sql
psql "$DATABASE_URL" -f app/sql/002_auth_resolver.sql
psql "$DATABASE_URL" -f app/sql/003_ingestion_tables.sql
psql "$DATABASE_URL" -f app/sql/004_capacity_management.sql
alembic stamp 0003_capacity_baseline
alembic upgrade head
psql "$DATABASE_URL" -f app/sql/005_performance_indexes.sql
```

**Em produção, isso é automático — ver seção seguinte.** Você não
precisa rodar nenhum desses comandos manualmente lá.

## Deploy em produção (Railway ou equivalente) — 100% automático
Depois de descobrirmos, na prática, que "rodar o SQL na mão uma vez e
depois deixar o Alembic seguir sozinho" é fácil de esquecer/errar em
produção — a solução final é `app/scripts/entrypoint.py`, um único
processo que faz TUDO sozinho, a cada deploy:

1. Cria o schema e as tabelas (se ainda não existirem).
2. Roda as migrations do Alembic.
3. Aplica os arquivos SQL pós-upgrade (005 a 009 — índices de
   performance, Platform Admin, Contract Intelligence, Recurso de
   Glosa, Destinatários de Relatórios) via marcador de tabela: cada
   arquivo só roda se a tabela que ele cria ainda não existir.
4. **Cria as roles de produção (`app_runtime`, `auth_resolver_owner`)
   automaticamente** — isso não é mais um passo manual.
5. Só então inicia a aplicação, já conectada pela role correta
   (`app_runtime`, sujeita a RLS — nunca o superusuário).

Tudo isso roda em TODO deploy, de forma idempotente (seguro repetir —
se já está tudo pronto, cada passo vira um no-op rápido).

> **Bug crítico de produção corrigido em 29/08/2026:** até esta versão,
> `bootstrap()` só aplicava `005_performance_indexes.sql`
> automaticamente — `006_platform_admin.sql`, `007_contract_intelligence.sql`
> e `008_denial_appeals.sql` ficavam documentados como "rode `psql -f`
> manualmente, depois `alembic stamp`", e esse passo manual nunca foi
> executado contra o banco de produção real. Como `alembic upgrade head`
> avança o carimbo de versão através dessas três migrations mesmo com
> `upgrade()`/`downgrade()` vazios (padrão deliberado deste projeto — ver
> DECISÃO nos próprios arquivos `alembic/versions/000{6,7,8}_*.py`), o
> banco ficava "marcado como atualizado" sem as tabelas/colunas
> existirem de fato. Resultado visível: TODA tela que dependia de
> `core.api_keys`, `core.insurance_companies`, `core.contract_items` ou
> `core.denial_appeals` mostrava "Algo deu errado" em produção (Usuários,
> Integrações, Convênios & Contratos, Sala de Comando, Painel, Recurso
> de Glosa) — só Minha Clínica funcionava, por não tocar nenhuma tabela
> nova. A correção: `bootstrap_db.py` agora aplica 006/007/008/009
> automaticamente em todo deploy, usando a existência de uma tabela
> "marcadora" de cada arquivo (`api_keys`, `insurance_companies`,
> `denial_appeals`, `report_recipients`) para decidir se já rodou — nunca
> mais depender de um passo manual que ninguém lembra de repetir.

### O que você precisa configurar — só isto, uma vez
No Railway → serviço da API → aba **Variables**, adicione três
variáveis (nenhuma outra é necessária; **não configure `DATABASE_URL`
manualmente** — o entrypoint calcula sozinho):

| Variável | Valor |
|---|---|
| `DATABASE_ADMIN_URL` | A `DATABASE_URL` que o Railway já dá para o Postgres (o usuário `postgres`, superusuário) — no Railway, você pode referenciar direto a variável do serviço de Postgres em vez de copiar/colar. |
| `APP_RUNTIME_PASSWORD` | Uma senha forte, gerada com `python -c "import secrets; print(secrets.token_urlsafe(32))"` — gere uma vez, cole aqui, guarde uma cópia. |
| `JWT_SECRET_KEY` | Qualquer string longa e aleatória (mesma geração acima serve). |

Opcionais — só o Parser Inteligente de Contratos depende delas; sem elas
o app sobe normalmente, e `POST /contracts/upload`/`/extract` devolvem
503 com mensagem explícita em vez de falhar silenciosamente:

| Variável | Valor |
|---|---|
| `AWS_S3_CONTRACTS_BUCKET` | Bucket S3 para os PDFs de contrato originais (separado do bucket de ingestão em lote). |
| `ANTHROPIC_API_KEY` | Chave da API da Anthropic usada na extração por IA (`contract_extraction_service.py`). |
| `CONTRACT_EXTRACTION_MODEL` | Modelo usado na extração — default `claude-sonnet-4-5`. |
| `AWS_S3_APPEALS_BUCKET` | Bucket S3 para anexos de Recurso de Glosa (separado dos outros dois — ver DECISÃO em `app/sql/008_denial_appeals.sql`). Sem ele, `POST /denial-appeals/{id}/attachments` devolve 503; abrir/protocolar/resolver um recurso funciona normalmente sem anexo nenhum. |
| `DEFAULT_APPEAL_DEADLINE_DAYS` | Fallback genérico (dias corridos) para o prazo de recurso quando a operadora ainda não tem `default_appeal_deadline_days` configurado — default `30`. **Não é uma norma da ANS**, é só para o campo nunca ficar em branco. |

E o `railway.toml` (já commitado) configura o comando de start:
```toml
startCommand = "python -m app.scripts.entrypoint"
```

### Por que não é um comando "bootstrap && uvicorn" encadeado
Essa foi a primeira tentativa, e tinha um problema real: a
`DATABASE_URL` calculada pelo bootstrap só chega até a aplicação se for
setada ANTES do primeiro import de `app.main` — e isso só é garantido
dentro do MESMO processo Python. Dois comandos encadeados por `&&` no
shell rodam como dois processos separados; uma variável setada num não
chega no outro. `entrypoint.py` faz os dois passos no mesmo processo,
de propósito.

### Se o banco já está num estado quebrado/parcial (de uma tentativa anterior)
Uma única vez, antes do primeiro deploy com este `entrypoint.py`, limpe
qualquer resíduo de tentativas manuais anteriores — rode isto uma vez,
via `psql` (Colab ou terminal), com a `DATABASE_ADMIN_URL`:
```bash
psql "$DATABASE_ADMIN_URL" -c "DROP SCHEMA IF EXISTS core CASCADE;"
```
A partir daí, o `entrypoint.py` assume sozinho, para sempre — inclusive
se você recriar o banco do zero no futuro.

Evoluções futuras de schema:
```bash
# depois de alterar/criar um model em app/models/
alembic revision --autogenerate -m "adiciona coluna X em patients"
alembic upgrade head
```

## Worker de ingestão (Etapa 1 do pipeline)
Roda como processo/container separado da API, mesmo codebase:
```bash
export SQS_INGESTION_QUEUE_URL="https://sqs.sa-east-1.amazonaws.com/.../ingestao-rcm"
python -m app.worker.ingestion_worker
```

**Fluxo:** SFTP deposita em `s3://bucket/tenants/{tenant_id}/incoming/{csv|xml|json}/arquivo`
→ S3 Event Notification publica em SQS (provisionado via Terraform, fora
deste repo) → o worker consome a fila, baixa o objeto, valida o tenant
contra `core.tenants`, faz o parsing (`app/worker/parsers/`) para o
schema canônico `RawBillingRow`, e grava em `core.ingestion_raw_rows`
(landing zone) com idempotência garantida por
`UNIQUE(tenant_id, s3_bucket, s3_key, s3_version_id)`.

A Etapa 2 (normalização — casar `insurance_plan_raw_name` com o
`insurance_plan_id` certo via `insurance_plan_aliases`, promover as
linhas para `patients`/`appointments`/`billing` de fato) roda logo em
seguida, dentro da mesma sessão tenant-aware — ver seção própria abaixo.

## Etapa 2 — normalização (`app/services/normalization_service.py`)
Chamada inline pelo próprio worker de ingestão, na MESMA sessão
tenant-aware (decisão deliberada: evita um segundo poller/fila para um
trabalho que, por linha, é rápido — ver docstring do arquivo para o
critério de quando valeria a pena extrair um worker dedicado no futuro).

Por linha (`RawBillingRow`), o fluxo é:
1. **Resolve o convênio** (`InsurancePlanRepository.resolve`): tenta por
   `normalized_key` exato (slug determinístico, ex: "UNIMED NAC." →
   `unimed_nac`), depois por uma variação já vista em
   `insurance_plan_aliases`. Se não encontrar, a linha fica `rejected`
   com motivo `unknown_insurance_plan` — **nunca criamos um convênio novo
   sozinhos** a partir de um texto ambíguo; fica para revisão humana na
   tela de Setup.
2. **Resolve ou cria o paciente** por CPF.
3. **Cria o `Appointment`** (status `completed` — dado importado já
   aconteceu) e o **`Billing`**, passando pelo **mesmo `denial_risk_engine`**
   que os endpoints da API usam — nenhuma lógica de risco duplicada.
4. Marca a linha como `normalized`, ou `rejected` se o convênio não foi
   resolvido.

Toda variação de convênio resolvida com sucesso é registrada em
`insurance_plan_aliases` (se ainda não vista), acelerando o match nas
próximas importações do mesmo tenant.

## Resolução manual de convênio desconhecido (tela de Setup)
`GET /api/v1/ingestion/rejected?reason=unknown_insurance_plan` — lista
linhas que a Etapa 2 não conseguiu promover sozinha.
`POST /api/v1/ingestion/rejected/{row_id}/resolve-insurance-plan` — o
humano escolhe o `insurance_plan_id` correto; o endpoint grava o alias
(`InsurancePlanRepository.record_alias_if_new`) e chama
`normalize_row()` de novo, reaproveitando o MESMO caminho de resolução —
sem lógica de promoção duplicada. **Resolve em lote**: outras linhas
`rejected` com o mesmo `raw_value` (comum quando o mesmo convênio mal
escrito se repete várias vezes no arquivo diário) são promovidas
automaticamente na mesma chamada, usando o alias que acabou de ser
gravado — o usuário não precisa repetir o mapeamento linha por linha.

## Capacity & Utilization Management (analytics de agenda)
Módulo aditivo — não exigiu tocar em `billing`, `denial_risk_engine` nem
no pipeline de ingestão. Escopo deliberadamente restrito nesta primeira
versão (ver decisão completa em `app/sql/004_capacity_management.sql`):

- **Granularidade por profissional** (`core.professionals`), não por
  clínica agregada nem por sala/recurso — é o nível que desbloqueia a
  maioria das perguntas de negócio reais (ocupação do Dr. X, no-show por
  médico) com uma única entidade nova.
- **Grade semanal recorrente** (`professional_availability`), não um
  calendário de exceções (feriado, férias) — fica para quando houver
  sinal de que a imprecisão importa na prática.
- **Sem sistema de reserva de sala/equipamento** — fica como extensão
  futura (`core.resources`), sem quebrar nada do que existe.

`GET /api/v1/capacity/utilization/{professional_id}?date_from=...&date_to=...`
retorna `utilization_rate` (minutos ocupados ÷ minutos disponíveis pela
grade) e `no_show_rate` como **métricas separadas de propósito** — uma
mede capacidade consumida, a outra mede receita perdida dentro da
capacidade já consumida (ver `app/services/capacity_service.py`).
`appointments` ganhou `professional_id` e `duration_minutes` (colunas
nullable — não quebra dado já existente).

## Alerta de Risco de Falta (Fase 1 — score por paciente)
Decisão de escopo tomada explicitamente com o cliente: só a Fase 1 (score
de risco calculado do histórico do próprio paciente) foi implementada. A
Fase 2 (fila de espera automatizada + convite instantâneo por WhatsApp)
foi deliberadamente adiada — exige coleta de dado novo (endereço do
paciente, para "mora perto"), a mesma integração de WhatsApp ainda não
construída (Etapa 4), e um desenho de concorrência ainda não feito
("quem garante a vaga primeiro" quando vários pacientes da fila são
convidados ao mesmo tempo).

`app/services/no_show_risk_engine.py` calcula, na criação de cada
`appointment` (`AppointmentService.create_appointment`), dois sinais a
partir do histórico PASSADO do paciente:
- **Taxa geral de falta** (mais amostras, mais estável).
- **Taxa específica do mesmo dia da semana + período** (manhã/tarde/noite)
  do agendamento sendo criado — só usada quando há pelo menos
  `MIN_SPECIFIC_SAMPLES=3` ocorrências naquela combinação exata, para não
  tirar conclusão de amostra pequena demais (1 falta em 1 ocorrência não
  vira "100% de risco").

Um paciente sem nenhum histórico recebe `no_show_risk_level="indeterminado"`,
não `"baixo"` — tratar ausência de dado como baixo risco seria uma
afirmação de confiança que os dados não sustentam. Cancelamentos ficam
fora da amostra: cancelar com antecedência é um comportamento diferente
de faltar sem avisar. Limiares de classificação (`_LOW_THRESHOLD=0.10`,
`_MEDIUM_THRESHOLD=0.30`) são valores de partida para MVP, não uma
calibração estatística validada — primeiro ponto a revisar com volume
real de dado.

`0004_add_no_show_risk_fields.py` é a primeira migration do projeto que
segue o fluxo NORMAL do Alembic (não um stamp de baseline) — as colunas
novas não envolvem RLS, então passam por `alembic upgrade head` como
qualquer migration comum.

## Etapa 4 — relatório semanal via WhatsApp
`app/worker/weekly_report_job.py` é um SCRIPT DE EXECUÇÃO ÚNICA (roda,
processa todos os tenants ativos com `whatsapp_group_id` configurado, e
termina) — diferente do `ingestion_worker.py`, que é um loop contínuo.
Disparado por um agendador EXTERNO (cron do host ou AWS EventBridge
Scheduler), não por um loop "dorme até sexta-feira" dentro da aplicação —
evitaria manter um container ligado 24/7 para acordar uma vez por semana.

**Duas restrições técnicas reais que mudam a expectativa do briefing original:**
1. **A API do WhatsApp não envia para "grupos"** — só para números
   individuais. `tenants.whatsapp_group_id` (nome herdado do briefing) é
   tratado como o número de destino de um responsável, não um grupo com
   várias pessoas. Documentado em `app/services/whatsapp_client.py`.
2. **Mensagem iniciada pela empresa exige template pré-aprovado** na Meta
   Business Manager (`settings.WHATSAPP_REPORT_TEMPLATE_NAME`) — não dá
   para simplesmente mandar um PDF como texto livre; isso é configuração
   de conta, feita fora deste repositório, antes de qualquer envio funcionar.

O relatório reaproveita tudo que já existe: `ReportingRepository`
(faturamento/glosa, ROI simplificado, no-show) + `CapacityService` (já
construído para o módulo de Capacidade) — nenhuma agregação nova foi
inventada além do que os módulos anteriores já calculavam.
`POST /api/v1/reports/weekly/send` dispara o mesmo relatório sob demanda
(admin/owner), útil para testar sem esperar o agendador externo.

## Webhook Meta Ads
`GET /api/v1/webhooks/meta-ads/{tenant_id}` — handshake de verificação
exigido pela Meta ao cadastrar a URL.
`POST /api/v1/webhooks/meta-ads/{tenant_id}` — recebe eventos, valida a
assinatura HMAC (`X-Hub-Signature-256`) contra `tenants.meta_ads_webhook_secret`
e grava em `core.marketing_webhook_events` com dedupe por `external_event_id`.
Cada tenant configura seu próprio segredo na tela de Setup do produto.

## Observabilidade e erros amigáveis
Duas audiências diferentes, resolvidas com o mesmo mecanismo (`app/main.py`):
- **Todo erro da API** (400 a 500) sai no mesmo formato:
  `{"error_code": "...", "message": "...", "request_id": "..."}`.
  `error_code` é uma chave estável para o frontend mapear em texto/ícone,
  sem depender de parsing de string em português. `message` já vem em
  português, sem jargão técnico — nunca uma stack trace crua.
  `request_id` é o número que o usuário pode citar numa mensagem de
  suporte.
- **Todo log é JSON estruturado** (`app/core/logging_config.py`), com o
  mesmo `request_id` em toda linha gerada durante aquele request — uma
  investigação de bug em produção começa em "todas as linhas desse
  request_id", não em "por volta de que horas foi isso?". `request_id`
  é gerenciado via `ContextVar` (`app/core/request_context.py`), correto
  sob concorrência assíncrona.
- Erro 500 (não tratado) loga o traceback completo (nível ERROR,
  `exc_info`) mas NUNCA retorna detalhe técnico ao cliente — só o
  `request_id` para correlação.
- `/health` agora testa uma conexão real com o banco, não só responde
  "ok" cegamente — um load balancer não deveria mandar tráfego pra uma
  instância com banco fora do ar.

### Sentry (monitoramento de erros — opcional)
Hoje um bug em produção só é descoberto quando um cliente reclama. O
Sentry cobre essa lacuna: quando configurado, uma exceção não tratada na
API (`app/main.py`), no worker de ingestão
(`app/worker/ingestion_worker.py`) ou em qualquer um dos dois crons de
disparo por WhatsApp (`app/worker/weekly_report_job.py`,
`app/worker/daily_alert_job.py`) é reportada automaticamente, sem
esperar ninguém ler o log.

> **BUG CORRIGIDO (rodada de monitoramento/alertas):** os dois crons de
> WhatsApp nunca chamavam `sentry_sdk.init()` — se o job inteiro
> quebrasse antes do laço por tenant (ex: `WhatsAppClient()` levantando
> `WhatsAppClientError` porque a credencial expirou), o processo morria
> com uma stack trace no log do container e **ninguém era avisado**,
> indistinguível de "rodou certo e não tinha nada para enviar". Corrigido
> nos dois arquivos, no mesmo padrão do worker de ingestão. Além disso,
> `app/services/report_send_service.py` ganhou
> `_alert_if_total_send_failure`: falha em **100% dos destinatários** de
> um tenant (não só de "um número inválido", que já era tolerado por
> design) agora vira log `ERROR` + alerta ativo no Sentry — sinal de
> problema sistêmico na integração (token expirado, template
> desaprovado no Meta Business Manager), não de dado ruim de um único
> destinatário.

- **Sem `SENTRY_DSN` configurada: nada muda.** Nenhuma chamada de
  `sentry_sdk.init()` acontece — o mesmo padrão de toda outra integração
  externa opcional deste projeto (`ANTHROPIC_API_KEY`,
  `AWS_S3_CONTRACTS_BUCKET` etc., ver `app/core/config.py`): ausência de
  configuração = feature desligada, nunca crash no boot.
- **Variáveis de ambiente:**
  - `SENTRY_DSN` — o DSN do projeto no Sentry. Ausente/vazio = desligado.
  - `SENTRY_TRACES_SAMPLE_RATE` (padrão `0.0`) — amostragem de
    performance tracing. Desligado por padrão porque tracing consome
    quota do plano rapidamente; ligar é decisão explícita do operador.
  - `SENTRY_PROFILES_SAMPLE_RATE` (padrão `0.0`) — mesma lógica, para
    profiling.
- **Com `SENTRY_DSN` configurada:** cada request da API ganha o
  `request_id` como tag do Sentry (o mesmo `request_id` que já aparece no
  log estruturado e na resposta de erro para o usuário) — dá pra ir do
  "código que o cliente citou no telefone" direto ao evento no Sentry.
  Quando a exceção acontece numa rota autenticada (depois da dependency
  `get_current_user`, ver `app/api/deps.py`), `tenant_id` e `role`
  também entram como tag. O worker de ingestão reporta ao Sentry as
  exceções não previstas do loop de processamento, SEM alterar o
  comportamento de retry/DLQ do SQS (a mensagem continua voltando a
  ficar visível e sendo reprocessada exatamente como antes — o reporte é
  só observabilidade adicional).
- **Privacidade — `send_default_pii=False` explícito:** o padrão do SDK
  do Sentry manda corpo de requisição e dados de usuário (nome/e-mail)
  em algumas integrações. Este é um sistema de saúde — dado de
  paciente/beneficiário não pode vazar para um serviço terceiro de
  monitoramento de erro. Por isso `send_default_pii=False` é setado
  explicitamente em ambos os pontos de inicialização (API e worker), e
  nenhum código deste projeto anexa manualmente corpo de request, e-mail
  de usuário ou dado clínico como contexto extra do Sentry — só tags
  técnicas de correlação (`request_id`, `tenant_id`, `role`).

## Performance — o que já foi corrigido e o que ainda falta
Dois achados reais de uma auditoria (não suposição):
- **Índices em `tenant_id` faltando** em `patients`, `contracts`,
  `insurance_plans`, `professionals`, `users` — como toda política de
  RLS filtra por `tenant_id`, a ausência de índice significa varredura
  sequencial da tabela INTEIRA (todos os tenants) a cada consulta.
  Corrigido em `app/sql/005_performance_indexes.sql`.
- **N+1 em `ProfessionalService.list_professionals`** — uma query de
  disponibilidade por profissional dentro de um loop. Corrigido com
  `ProfessionalAvailabilityRepository.list_by_professionals` (uma query
  em lote, agrupada em Python).
- **Rate limiting em memória não escala para múltiplas instâncias** —
  cada instância teria seu próprio contador. `RATE_LIMIT_STORAGE_URI`
  (ex: Redis) resolve isso quando houver mais de uma instância atrás de
  um load balancer — hoje é `None` (memória), suficiente para instância
  única.
- **Connection pool dimensionado para desenvolvimento** (`pool_size=10`)
  — em produção com tráfego concorrente real, considerar PgBouncer ou
  RDS Proxy antes de simplesmente aumentar o pool da aplicação.

## Rodando os testes de integração
A pasta `tests/integration/` cobre a aplicação de ponta a ponta via HTTP
(login, RLS entre tenants, RBAC, motor de glosa, capacidade, risco de
falta) — diferente dos testes unitários na raiz de `tests/`, que são
funções puras sem banco. **Precisa de um Postgres real**: RLS não existe
em SQLite, então rodar isso contra SQLite daria falsa confiança
justamente na garantia de segurança mais crítica do projeto.

`tests/conftest.py` cria um banco descartável a cada execução da suíte,
aplica `app/sql/001` a `004` + a migration `0004` do Alembic, e — pela
primeira vez em todo o projeto — executa de verdade as roles que até
então só existiam COMENTADAS em `001_init_schema.sql`/`002_auth_resolver.sql`
(`app_test_runtime`, sem `BYPASSRLS`; e a dona da função `resolve_login`,
com `BYPASSRLS` restrito a essa única função). A aplicação, durante os
testes, conecta como `app_test_runtime` — **nunca como superusuário** —
porque um superusuário do Postgres ignora RLS por definição, o que faria
os testes de isolamento "passarem" mesmo com RLS quebrado.

### Opção A — máquina local com Docker
```bash
docker compose up -d db
export TEST_DATABASE_ADMIN_URL="postgresql://postgres:postgres@localhost:5432/postgres"
pip install -r requirements.txt
pytest tests/integration -v
```

### Opção B — Google Colab (sem Docker disponível)
```python
!apt-get -qq update && apt-get -qq install -y postgresql
!service postgresql start
!sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"

import os
os.environ["TEST_DATABASE_ADMIN_URL"] = "postgresql://postgres:postgres@localhost:5432/postgres"

!pip install -q -r requirements.txt
!pytest tests/integration -v
```

Sem `TEST_DATABASE_ADMIN_URL` configurada, os testes de integração são
**pulados** (não falham) — só os testes unitários da raiz de `tests/`
rodam. Isso evita quebrar quem só quer rodar a suíte rápida sem subir banco.

### O que ainda não está coberto
- O worker de ingestão (`ingestion_worker.py`) e o job de relatório
  semanal (`weekly_report_job.py`) — os SCRIPTS em si, não os endpoints
  — não têm teste de integração: exigiriam mockar S3/SQS, escopo maior,
  fica para uma rodada dedicada. Os endpoints HTTP que ELES alimentam
  (`/ingestion/rejected`, `/reports/weekly/send`) já estão cobertos.
- Fluxo de IA de contratos (`/contracts/upload`, `/extract`,
  `/homologate`): a função pura de validação (`validate_extracted_items`)
  tem 10 testes unitários (`test_contract_extraction_service.py`), mas o
  fluxo ponta-a-ponta via HTTP depende de S3 e da API da Anthropic reais
  — cobertura de integração fica para quando essas credenciais existirem
  num ambiente de teste (hoje `AWS_S3_CONTRACTS_BUCKET`/`ANTHROPIC_API_KEY`
  não configuradas fazem os dois primeiros passos devolver 503 de forma
  explícita, nunca um erro silencioso).

### Cobertura de endpoints (tests/integration/)
| Endpoint | Cobertura |
|---|---|
| `auth` (login) | ✅ `test_auth.py` |
| `patients` | ✅ `test_rls_isolation.py`, `test_rbac.py` |
| `appointments` | ✅ `test_rls_isolation.py`, `test_no_show_risk.py` |
| `billing` | ✅ `test_billing_denial_engine.py` (create + high-risk) |
| `contracts` | ✅ `test_contracts.py` + RBAC em `test_rbac.py` (cadastro manual; fluxo de upload/extração/homologação por IA ainda sem cobertura de integração — depende de S3/Anthropic reais, ver "O que ainda não está coberto") |
| `denial-appeals` | ✅ `test_denial_appeals.py` (ciclo de vida aberto→protocolado→deferido/indeferido/NIP, cálculo de prazo, RBAC) — upload de anexo real (depende de `AWS_S3_APPEALS_BUCKET`) sem cobertura de integração, mesma ressalva do PDF de contrato |
| `professionals` | ✅ `test_professionals.py` + RBAC em `test_rbac.py` |
| `capacity` | ✅ `test_capacity_utilization.py` |
| `ingestion` (resolução de convênio) | ✅ `test_ingestion_resolution.py` |
| `webhooks` (Meta Ads) | ✅ `test_webhooks.py` (assinatura HMAC, handshake, dedupe) |
| `reports` (relatório semanal) | ✅ `test_reports.py` (com mock do WhatsApp) |
| `report-recipients` (Gestão de Contatos para Relatórios) | ✅ `test_report_recipients.py` (CRUD, RBAC, validação "pelo menos um contato") |
| `audit-log` (Logs de Auditoria) | ✅ `test_audit_log.py` (listagem paginada, filtros, RBAC) |

## Próximos passos sugeridos
- Criar as roles de banco `app_runtime` (RLS forçado) e o dono da função
  `resolve_login` via Terraform/IaC — nunca via SQL manual em produção.
- Provisionar o agendador externo do relatório semanal (cron/EventBridge
  Scheduler) e configurar o template no Meta Business Manager.
- Capacity: se houver sinal de necessidade real, evoluir para
  `core.resources` (salas/equipamentos) e/ou calendário de exceções
  (feriados, férias) sobre a grade recorrente.
- Fase 2 do Alerta de Risco de Falta (fila de espera + convite
  automático) — exige: coleta de endereço/localização do paciente,
  integração de envio via WhatsApp (mesma base da Etapa 4), e desenho de
  concorrência para "quem garante a vaga" quando vários pacientes da
  fila são convidados ao mesmo tempo.
- Recalibrar os limiares de `no_show_risk_engine.py` com volume real de
  dado assim que houver.
- Testes de integração para os SCRIPTS de worker (`ingestion_worker.py`,
  `weekly_report_job.py`) — hoje só os endpoints HTTP que eles alimentam
  são testados; os workers em si exigiriam mockar S3/SQS/WhatsApp.
