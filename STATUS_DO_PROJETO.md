# RCM/ERP Médico — Status do Projeto e Veredito

**Data:** 28/08/2026
**Escopo deste documento:** o que foi construído, o que foi validado de verdade, o que ainda falta, e se dá para começar o frontend.

---

## 1. Veredito executivo

**O backend tem uma base sólida e validada — mas não é um projeto de produção ainda.**

São duas afirmações diferentes, e a distância entre elas é o assunto principal deste documento:

- ✅ **A arquitetura está certa.** Multi-tenancy com isolamento garantido pelo banco (RLS), não pela aplicação. RBAC como segunda camada de defesa. Motores de regra de negócio (glosa, capacidade, risco de falta) desacoplados de infraestrutura, testáveis isoladamente.
- ✅ **42 de 42 testes de integração passam contra um Postgres real** — não simulado, não mockado. Isso inclui o teste que prova que a Clínica B fisicamente não consegue ver dado da Clínica A, mesmo tentando pelo ID direto.
- ❌ **Nunca foi implantado em lugar nenhum.** Nenhum container Docker rodou de ponta a ponta, nenhuma infraestrutura de nuvem foi provisionada, nenhum usuário real usou isso.
- ❌ **As roles de banco de produção nunca foram criadas de verdade** — só as roles de *teste* (`app_test_runtime`, `auth_resolver_owner_test`) existem e foram validadas.
- ❌ **O Alembic, como ferramenta, nunca rodou.** As migrations foram *espelhadas* manualmente nos testes (SQL direto via asyncpg), não executadas via `alembic upgrade head`.

Resumo em uma frase: **isto é uma fundação de engenharia correta e testada, pronta para a próxima fase (provisionar infraestrutura real e implantar em um ambiente de staging) — não uma aplicação em produção.**

---

## 2. O que foi construído

O projeto nasceu de um pedido simples (DDL inicial com RLS) e cresceu, turno a turno, para um backend completo de RCM/ERP médico. Números atuais:

- **86 arquivos Python** em `app/`
- **10 grupos de endpoints** (auth, patients, appointments, billing, contracts, professionals, capacity, ingestion, webhooks, reports)
- **4 scripts SQL versionados** (`001` a `004`) + **1 migration real do Alembic** (`0004`, colunas de risco de falta)
- **12 arquivos de teste de integração** + 5 arquivos de teste unitário (motores de regra puros)

### 2.1 — Núcleo: multi-tenancy e segurança (Etapa 0)
- Isolamento por Row-Level Security no Postgres — a Clínica A nunca vê dado da Clínica B, garantido pelo banco.
- `FORCE ROW LEVEL SECURITY` — nem o dono da tabela escapa da regra.
- Função `SECURITY DEFINER` para resolver login sob RLS (problema do "ovo e galinha": no login ainda não se sabe o tenant).
- Autenticação JWT, RBAC com 5 papéis (`owner`, `admin`, `financeiro`, `atendimento`, `auditor`).
- Middleware de tenant implementado como cadeia de `Depends()` do FastAPI (não middleware ASGI) — decisão deliberada para garantir que `SET LOCAL` e as queries do request compartilhem a mesma transação.

### 2.2 — Motor de glosa (Etapa 3 do pipeline original)
- Regras determinísticas e explicáveis (não caixa-preta): CID ausente, procedimento ausente, convênio sem contrato de referência, valor cobrado acima do contratado.
- Calcula `value_saved_by_correction` — o número que vira manchete no relatório da diretoria.

### 2.3 — Ingestão de dados (Etapa 1) + Normalização (Etapa 2)
- Worker assíncrono consumindo fila SQS alimentada por S3 Event Notification.
- Parsers para CSV, XML (protegido contra XXE via `defusedxml`) e JSON, todos convergindo para um schema canônico.
- Idempotência garantida por constraint de banco (`UNIQUE`), não por lógica de aplicação.
- Normalização resolve convênio por variações de texto já vistas (`insurance_plan_aliases`), com endpoint de resolução manual para os casos que a automação não resolve sozinha — inclusive resolução em lote.

### 2.4 — Capacidade operacional
- Modelagem por profissional (não por sala/recurso — decisão de escopo documentada).
- Grade semanal recorrente, cálculo de taxa de utilização e taxa de no-show separadas (são diagnósticos diferentes).

### 2.5 — Alerta de risco de falta (Fase 1)
- Calculado na criação de cada agendamento, a partir do histórico do próprio paciente.
- Distingue taxa geral de taxa específica por dia-da-semana+período, com limiar mínimo de amostra para não tirar conclusão precipitada.
- Fase 2 (fila de espera + convite automático) foi conscientemente adiada — decisão registrada, não esquecida.

### 2.6 — Relatório semanal via WhatsApp (Etapa 4)
- Reaproveita todos os motores acima (glosa, capacidade, ROI de marketing, no-show) num único PDF.
- Restrições reais da API do WhatsApp documentadas explicitamente (não envia para "grupos", exige template pré-aprovado).

### 2.7 — Webhook do Meta Ads
- Assinatura HMAC validada sobre os bytes crus do corpo, dedupe de eventos reenviados.

---

## 3. O que foi validado de verdade (e como)

Isso é o que muda depois desta conversa: **quase tudo abaixo só foi confirmado porque você rodou de verdade, não porque eu revisei com cuidado.** Eu não tinha Postgres, FastAPI ou pytest disponíveis no meu ambiente — validação real só aconteceu no seu Colab.

| O que foi provado | Como |
|---|---|
| RLS isola tenants de verdade | Teste direto no banco, conectando como a mesma role que a aplicação usa |
| Login funciona sob RLS (função SECURITY DEFINER) | Teste com dois tenants simultâneos no banco |
| RBAC bloqueia papel errado | Testes por papel, nas 3 rotas mais sensíveis |
| Motor de glosa calcula certo end-to-end | 4 testes cobrindo CID ausente, valor acima do contrato, caso limpo, listagem de alto risco |
| Isolamento de convênio, contrato, profissional | Testes dedicados por domínio |
| Assinatura HMAC do webhook | Teste com assinatura válida e inválida, dedupe |
| Relatório semanal gera PDF e "envia" (mockado) | Teste com WhatsApp mockado via `monkeypatch` |
| App sobe sem erro de import/configuração | Consequência de todos os testes rodarem |

### Bugs reais encontrados só porque isso rodou de verdade
Vale registrar, porque é sinal de que o processo funcionou como deveria:
1. Conflito `Depends`/`Annotated` no FastAPI (bloqueava a aplicação inteira de subir).
2. `email-validator` faltando no `requirements.txt`.
3. `asyncpg` sem wheel pré-compilado para a versão do Python do ambiente.
4. Ordem de criação da extensão `citext` — depois da tabela que dependia dela.
5. Bug de concatenação na construção da DSN de teste.
6. `BYPASSRLS` não cobre GRANTs básicos de schema/tabela — a função de login falhava por falta de `USAGE`/`SELECT`, mesmo com bypass de RLS setado.
7. Rate limit do login fixo em `"5/minute"` no código — sufocava os próprios testes.
8. **O bug mais sistêmico**: 19 colunas de data/hora em 15 models usando `Mapped[datetime]` puro, sem `DateTime(timezone=True)` nem `server_default` — causava tanto violação de `NOT NULL` quanto erro de `offset-naive vs. offset-aware`.

Nenhum desses apareceria numa revisão de código, por mais cuidadosa que fosse — só apareceram porque o código rodou contra infraestrutura real.

---

## 4. O que ainda falta para ser "produção"

Lista honesta, sem infravalorizar o trabalho que falta:

### 4.1 — Infraestrutura nunca provisionada
- Roles de banco de produção (`app_runtime`, dono de `resolve_login`) — só existem como roles de *teste*.
- Bucket S3 + fila SQS + redrive policy (DLQ) da Etapa 1.
- Agendador externo (cron/EventBridge) da Etapa 4.
- Conta do WhatsApp Business + template aprovado na Meta.
- Terraform/IaC para tudo isso — hoje é só documentação de intenção nos comentários do SQL.

### 4.2 — Nunca implantado
- Nenhum `docker-compose` de produção, nenhum Dockerfile da aplicação em si (só o do Postgres de teste).
- Nenhum ambiente de staging.
- Nenhuma pipeline de CI/CD — os 42 testes rodaram manualmente no seu Colab, não automaticamente a cada commit.

### 4.3 — Ferramentas de operação usadas só parcialmente
- **Alembic nunca rodou de verdade.** A migration `0004` foi espelhada como SQL direto nos testes — a ferramenta em si (`alembic upgrade head`) nunca foi invocada contra um banco.
- Os workers (`ingestion_worker.py`, `weekly_report_job.py`) nunca executaram — só os efeitos que eles *produziriam* foram testados (semeando dado direto no banco).

### 4.4 — Não implementado (escopo conscientemente adiado)
- Fase 2 do risco de falta (fila de espera + convite automático).
- Capacidade por sala/recurso, calendário de exceções.
- Tradução de evento do webhook Meta Ads → linha em `marketing_spend`.

### 4.5 — Faltando por completo
- Observabilidade: logging estruturado, métricas, alertas.
- Estratégia de secrets em produção (hoje são variáveis de ambiente sem gestão formal).
- Testes de carga/performance.
- Plano de backup e disaster recovery.
- Auditoria de segurança além do RLS (scan de dependências vulneráveis, etc.).

---

## 5. Podemos começar o frontend?

**Sim — com uma condição.** O contrato da API (rotas, schemas de entrada/saída, códigos de erro) está estável e testado. `/docs` (Swagger, gerado automaticamente pelo FastAPI) já é usável como referência viva para quem for construir o frontend.

O que muda a resposta de "sim" para "sim, com ressalva":
- Endpoints ligados a escopo **conscientemente adiado** (fila de espera, capacidade por sala) não existem — o frontend não deve assumir que vão aparecer no curto prazo.
- Sem staging real, o frontend vai precisar apontar para o mesmo tipo de ambiente efêmero que os testes usam (ou você provisiona um staging simples primeiro — recomendo isso antes de começar o frontend a sério, não depois).

**Minha recomendação de sequência:** provisionar um staging mínimo (Postgres real gerenciado + a aplicação rodando em algum lugar acessível, nem que seja um único servidor) antes de começar o frontend "para valer" — não porque o contrato da API vá mudar muito, mas porque desenvolver frontend contra localhost/Colab é frágil e vai te fazer perder tempo depois trocando URLs e credenciais. Isso não precisa ser caro nem demorado — é o próximo passo natural, não um bloqueador de meses.

---

## 6. Recomendação de próximos passos, em ordem

1. **Provisionar staging mínimo**: banco Postgres gerenciado (RDS, Supabase, etc.) + as roles de produção de verdade (não as de teste) + a aplicação rodando nele.
2. **Rodar `alembic upgrade head` pela primeira vez, de verdade**, contra esse staging — depois dos scripts SQL manuais.
3. **Configurar CI** (GitHub Actions ou similar) para rodar os 42 testes a cada `push` — hoje isso só acontece quando você lembra de rodar manualmente.
4. Só então: **começar o frontend**, apontando para o staging.
5. Em paralelo, provisionar a infraestrutura da Etapa 1/4 (S3, SQS, WhatsApp) quando o worker de ingestão for a próxima prioridade real de produto.

---

*Este documento reflete o estado do projeto ao final desta sessão de desenvolvimento. Foi escrito para ser lido por alguém decidindo se é hora de investir em frontend ou infraestrutura — não é um relatório de vendas do projeto.*
