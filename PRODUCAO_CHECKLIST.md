# O que falta para produção profissional

## Resposta direta: o sistema está pronto para 100 clientes?

**O código agora está preparado para isso — a infraestrutura de deploy ainda não.** São coisas diferentes, e vale separar:

- **No nível de código/query**, depois das correções desta sessão (índices em `tenant_id`, N+1 resolvido, rate limiting pronto para múltiplas instâncias), não vejo mais nenhum padrão que degradaria mal especificamente com 100 tenants — RLS bem indexado escala de forma previsível.
- **No nível de infraestrutura**, a resposta muda: hoje a aplicação roda como um único processo, com pool de conexão dimensionado para desenvolvimento (`pool_size=10`), sem PgBouncer, sem múltiplas instâncias atrás de load balancer. **Isso não é sobre 100 clientes especificamente — é sobre qualquer volume de tráfego concorrente real**, que só aparece quando você de fato tem clientes usando ao mesmo tempo. Não dá para "testar" isso sem infraestrutura de produção rodando; dá para *preparar* o código para não quebrar quando a hora chegar (o que fizemos agora) e *desenhar* a infraestrutura corretamente quando for provisionada (PgBouncer/RDS Proxy, múltiplas instâncias, Redis para rate limit).

Ou seja: o gargalo real para "100 clientes" hoje não é o código, é que **a decisão de arquitetura de deploy (quantas instâncias, connection pooling, banco gerenciado) ainda não foi tomada nem testada** — isso é Tier 1 (infraestrutura provisionada), não uma questão de otimização de código pendente.

---

Organizado em 3 camadas: **Tier 1** (bloqueadores para qualquer ambiente com usuário real, mesmo poucos), **Tier 2** (obrigatório antes de dado real de paciente — isto é HealthTech, LGPD entra aqui), **Tier 3** (necessário para escalar com confiança, não bloqueia o primeiro cliente).

---

## Tier 1 — Bloqueadores reais (mesmo com 1 cliente)

### Infraestrutura provisionada de verdade
- [ ] Terraform/IaC para: VPC, RDS Postgres (ou equivalente gerenciado), S3, SQS + DLQ, Secrets Manager.
- [ ] **Roles de banco de produção criadas via Terraform** (`app_runtime`, dono de `resolve_login`) — hoje só existem como roles de *teste*, recriadas e destruídas a cada rodada de `pytest`.
- [ ] Onde a aplicação vai rodar (ECS/Fargate, Cloud Run, VM) + load balancer + TLS.
- [ ] Container registry + Dockerfile da aplicação em si (hoje só existe `docker-compose.yml` do Postgres de teste).

### Deploy e migrations
- [ ] **Rodar `alembic upgrade head` pela primeira vez de verdade**, contra um banco recém-criado, validando que o processo documentado (`001`→`004` manual + `alembic stamp` + `alembic upgrade head`) funciona do zero. Hoje isso nunca aconteceu — foi só espelhado manualmente nos testes.
- [ ] Passo de migration automatizado no pipeline de deploy, com plano de rollback.

### CI
- [ ] Os 42 testes rodando automaticamente a cada `push`/PR (GitHub Actions ou similar, com Postgres como serviço). Hoje só rodam quando alguém lembra de rodar manualmente no Colab.
- [ ] Falha de teste bloqueia merge.

### Segurança básica de aplicação
- [ ] `CORS allow_origins` trocado do placeholder (`https://app.seudominio.com.br`) para o domínio real do frontend.
- [ ] `JWT_SECRET_KEY` gerado com entropia real e vindo de Secrets Manager, nunca hardcoded ou em `.env` versionado.
- [ ] TLS entre a aplicação e o Postgres (`asyncpg` com SSL) — hoje a conexão não força SSL.
- [ ] `/health` hoje só responde `{"status": "ok"}` sem checar conexão com banco — não serve para detectar banco fora do ar.

---

## Tier 2 — Obrigatório antes de dado real de paciente (LGPD/HealthTech)

### A auditoria que foi desenhada mas nunca implementada
- [ ] **`core.audit_log` existe desde o primeiro DDL, com a justificativa "auditoria é obrigatória em HealthTech" — mas nada no código escreve nela.** Isso precisa ser resolvido antes de qualquer dado real de paciente entrar no sistema: quem acessou o quê, quando, e o quê mudou em `billing`/`patients` precisa ficar registrado de verdade, não só ter uma tabela pronta esperando.

### LGPD especificamente
- [ ] Política de retenção e exclusão de dado de paciente (CPF, CID, nome) — o que acontece quando uma clínica cancela a assinatura, ou um paciente pede exclusão?
- [ ] Criptografia em repouso no banco (RDS oferece nativamente — precisa ser ligado explicitamente).
- [ ] Revisão de quem, dentro da equipe, tem acesso de produção ao banco (mesmo você, como desenvolvedor, acessando diretamente é um evento que devia ficar registrado).
- [ ] Termo de uso / política de privacidade alinhados com o que de fato é armazenado (CID é dado de saúde sensível).

### Segurança além do RLS
- [ ] Scan de dependências vulneráveis (`pip-audit`, Dependabot) — nenhuma das ~30 dependências do `requirements.txt` foi auditada quanto a CVEs.
- [ ] Scan de segredo acidental commitado (`gitleaks` ou similar) no CI.
- [ ] Revisão de segurança/pentest antes de dado real de paciente — RLS prova isolamento entre tenants, mas não prova ausência de outras vulnerabilidades (ex: IDOR em endpoints que ainda não testamos exaustivamente).

---

## Tier 3 — Necessário para escalar com confiança (não bloqueia o primeiro cliente)

### Observabilidade — IMPLEMENTADO NESTA SESSÃO
- [x] Logging estruturado (JSON) com `request_id` correlacionável, via `ContextVar`.
- [x] Envelope de erro único e amigável para o usuário final (`error_code` + `message` em português + `request_id`) — em vez de stack trace crua ou "Internal Server Error".
- [x] `/health` agora testa conexão real com o banco, não responde "ok" cegamente.
- [ ] Agregação centralizada de log (CloudWatch Logs ou equivalente) — o JSON já está pronto para isso, falta só apontar o `StreamHandler` para o destino certo em produção.
- [ ] Métricas (latência por endpoint, profundidade da fila SQS) e alertas — ainda não implementado, precisa de infraestrutura (Prometheus/CloudWatch Metrics) que não existe ainda.
- [ ] Tracing distribuído (OpenTelemetry) — o `request_id` já dá correlação básica; tracing formal é passo seguinte quando a cadeia de chamadas ficar mais complexa.

### Performance — IMPLEMENTADO NESTA SESSÃO
- [x] Índices em `tenant_id` para `patients`, `contracts`, `insurance_plans`, `professionals`, `users` — faltavam desde o início, cresceriam como lentidão silenciosa com volume de dado acumulado.
- [x] N+1 corrigido em `ProfessionalService.list_professionals`.
- [x] Rate limiting preparado para múltiplas instâncias (`RATE_LIMIT_STORAGE_URI` configurável para Redis) — hoje ainda em memória (correto para 1 instância).
- [ ] Connection pooling de produção (PgBouncer ou RDS Proxy) — ainda dimensionado para desenvolvimento (`pool_size=10`).

### Operação
- [ ] Runbooks: "o que fazer quando a DLQ tem mensagens", "o que fazer quando o webhook do WhatsApp começa a falhar".
- [ ] Backup testado de verdade (não só "o RDS faz backup automático" — testar uma restauração real pelo menos uma vez).

### Testes que ainda faltam
- [ ] Os workers (`ingestion_worker.py`, `weekly_report_job.py`) nunca executaram de verdade — só os efeitos que eles produziriam foram testados, semeando dado direto no banco. Precisa de teste com S3/SQS reais (ou LocalStack) pelo menos uma vez.
- [ ] Teste de carga — nunca sabemos o comportamento sob 50 requisições simultâneas de login, por exemplo.
- [ ] Teste com servidor real rodando (`uvicorn` + requisição HTTP de fora do processo) — hoje os testes usam `ASGITransport` em processo, que é rápido mas não passa pela pilha de rede real.

---

## O que NÃO está nesta lista (e por quê)

Coisas que já foram feitas com cuidado e não precisam de trabalho adicional agora:
- Isolamento multi-tenant (RLS) — testado no banco real, é a parte mais difícil de acertar e já está certa.
- RBAC — testado por papel.
- Motores de regra de negócio (glosa, capacidade, risco de falta) — puros, testados isoladamente, sem dependência de infraestrutura.
- Estrutura de camadas (endpoint → service → repository) — consistente em todo o projeto.

---

## Resumo em uma frase por tier

- **Tier 1**: sem isso, você não consegue nem colocar isso no ar de forma responsável.
- **Tier 2**: sem isso, você não deveria deixar dado real de paciente entrar no sistema.
- **Tier 3**: sem isso, funciona com um cliente — quebra ou fica difícil de operar com vários.
