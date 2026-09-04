"""
app/core/config.py

DECISÃO ARQUITETURAL — Configuração centralizada via pydantic-settings
-----------------------------------------------------------------------
Toda credencial e parâmetro sensível (URL do banco, segredo JWT, chaves de
API do Meta Ads) entra pelo AMBIENTE (variáveis de env / secrets manager),
nunca hardcoded no código-fonte. O pydantic-settings valida os tipos no
boot da aplicação: se faltar uma variável obrigatória, a app falha ao
subir (fail-fast) em vez de falhar silenciosamente em produção às 3h da
manhã. Isso também facilita ter .env diferentes por ambiente
(dev/staging/prod) sem tocar em código.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # --- Aplicação ---
    ENVIRONMENT: str = "development"
    API_V1_PREFIX: str = "/api/v1"

    # --- Banco de dados ---
    # Duas roles distintas de conexão (ver DECISÃO #8 do DDL):
    # DATABASE_URL      -> role "app_runtime": sujeita a RLS, usada em 99% das queries.
    # DATABASE_URL_AUTH -> role restrita, usada SOMENTE pelo resolver de login
    #                      (via função SECURITY DEFINER; ver app/sql/002_auth_resolver.sql).
    #                      Não precisa ser uma role de banco diferente — a função
    #                      SECURITY DEFINER já resolve o bypass de RLS de forma
    #                      auditável. Mantemos a URL separada aqui apenas para
    #                      permitir, se necessário, apontar para um pooler distinto.
    DATABASE_URL: str
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 5

    # --- Bootstrap automático (app/scripts/entrypoint.py) ---
    # DATABASE_ADMIN_URL: conexão de SUPERUSUÁRIO, usada SÓ no arranque do
    # processo para criar schema/roles/migrations — nunca usada para
    # servir requisição alguma depois disso. APP_RUNTIME_PASSWORD: a senha
    # da role restrita (app_runtime) que o entrypoint cria/usa
    # automaticamente. Nenhuma das duas é lida pela aplicação em si (só
    # pelo script de bootstrap, via os.environ direto — ver DECISÃO em
    # app/scripts/bootstrap_db.py sobre por que não usar Settings() aqui).
    # Declaradas aqui só para ficarem documentadas/descobríveis num único
    # lugar.
    DATABASE_ADMIN_URL: str | None = None
    APP_RUNTIME_PASSWORD: str | None = None

    # --- Segurança / JWT ---
    JWT_SECRET_KEY: str          # nunca commitar: vem de Secrets Manager / Vault
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # --- Rate limiting ---
    RATE_LIMIT_DEFAULT: str = "100/minute"
    # Limite específico do login (proteção contra brute-force de senha).
    # Configurável em vez de fixo no código — nos testes de integração,
    # todas as chamadas HTTP compartilham o mesmo "IP" simulado (cliente
    # ASGI em processo), então um valor fixo baixo estourava o limite já
    # nos primeiros testes. Em produção, o padrão continua restritivo.
    LOGIN_RATE_LIMIT: str = "5/minute"
    # Mesma proteção contra abuso, aplicada aos dois novos endpoints
    # públicos (self-signup e recuperação de senha) — sem isso, os dois
    # seriam um vetor óbvio de spam (criar tenants em massa) ou de
    # enumeração de e-mail por força bruta de tentativas.
    REGISTER_RATE_LIMIT: str = "5/minute"
    PASSWORD_RESET_RATE_LIMIT: str = "5/minute"
    # None = contagem em memória do processo (só funciona com 1 instância
    # da aplicação). Setar para "redis://host:6379" quando houver mais de
    # uma instância atrás de um load balancer — ver DECISÃO em
    # app/core/rate_limit.py.
    RATE_LIMIT_STORAGE_URI: str | None = None

        # --- CORS ---
    CORS_ALLOWED_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()]

    # --- Integrações externas (Etapa 1 do pipeline) ---
    META_ADS_APP_SECRET: str | None = None
    # Bucket de ingestão de lotes operacionais (CSV/XML/JSON). Dois
    # caminhos de ENTRADA gravam aqui, com a MESMA convenção de chave
    # (ver app/worker/s3_key_resolver.py): 1) SFTP -> S3 Event
    # Notification -> SQS -> app/worker/ingestion_worker.py (o worker em
    # si recebe o nome do bucket vindo do próprio evento S3, não lê esta
    # setting diretamente); 2) POST /ingestion/upload (novo caminho HTTP
    # síncrono, ver app/services/ingestion_storage_client.py), que LÊ
    # esta setting e faz 503 explícito se ela não estiver configurada —
    # nunca reintroduzimos um bucket separado só para o upload HTTP,
    # exatamente porque é o MESMO tipo de objeto processado pelo MESMO
    # pipeline (ver app/services/ingestion_processing_service.py).
    AWS_S3_INGEST_BUCKET: str | None = None
    # URL da fila SQS que recebe as S3 Event Notifications do bucket de
    # ingestão (ver app/worker/ingestion_worker.py). Provisionada via
    # Terraform/IaC junto com o bucket e a notificação — não faz parte
    # do escopo deste backend.
    SQS_INGESTION_QUEUE_URL: str | None = None

    # --- Parser Inteligente de Contratos (IA) ---
    # Bucket separado do de ingestão de lotes (AWS_S3_INGEST_BUCKET): o
    # PDF de contrato é enviado DIRETO pela tela (upload HTTP síncrono via
    # app/services/contract_storage_client.py), não pelo fluxo SFTP->S3
    # Event->SQS da Etapa 1 — são dois pipelines de origem diferente
    # gravando em dois lugares diferentes, embora ambos usem S3.
    AWS_S3_CONTRACTS_BUCKET: str | None = None
    # Credencial da PLATAFORMA para chamar a API de extração (Anthropic) —
    # nunca do tenant. Ver app/services/contract_extraction_service.py.
    ANTHROPIC_API_KEY: str | None = None
    CONTRACT_EXTRACTION_MODEL: str = "claude-sonnet-4-5"

    # --- Recurso de Glosa (conformidade ANS) ---
    # Bucket separado dos outros dois (mesma lógica de sempre: natureza e
    # retenção diferentes — aqui são comprovantes de protocolo/anexos
    # exigidos pela operadora para o recurso, não o PDF do contrato nem
    # os lotes de faturamento importados).
    AWS_S3_APPEALS_BUCKET: str | None = None

    # Endpoint S3 customizado — ausente/None em produção real (boto3
    # resolve a AWS sozinho). Existe só para apontar os três buckets
    # acima para um serviço S3-compatível (ex: MinIO) num ambiente sem
    # AWS de verdade, como um deploy de teste no Railway (ver DECISÃO
    # completa em app/core/aws_s3.py).
    AWS_S3_ENDPOINT_URL: str | None = None

    # DECISÃO — prazo de recurso é CONTRATUAL/operadora, não uma lei
    # federal única
    # ---------------------------------------------------------------
    # Diferente de reembolso ao beneficiário (onde a ANS regula prazo),
    # o prazo para a CLÍNICA contestar uma glosa é definido no contrato
    # entre clínica e operadora — varia por operadora e por convênio.
    # Este valor é só um FALLBACK genérico quando o tenant ainda não
    # configurou `default_appeal_deadline_days` na operadora específica
    # (ver app/models/insurance_company.py) — nunca deve ser tratado como
    # "a lei diz 30 dias". A tela de Operadoras deve deixar claro que o
    # usuário precisa confirmar esse número no contrato real.
    DEFAULT_APPEAL_DEADLINE_DAYS: int = 30

    # --- WhatsApp Business Cloud API (Etapa 4 — relatório semanal) ---
    # Credenciais da CONTA DA PLATAFORMA (nós somos quem envia), não do
    # tenant — cada tenant só configura o número de destino
    # (tenants.whatsapp_group_id) e, futuramente, poderia ter seu próprio
    # remetente se o produto crescer para permitir múltiplos números.
    WHATSAPP_ACCESS_TOKEN: str | None = None
    WHATSAPP_PHONE_NUMBER_ID: str | None = None
    WHATSAPP_API_VERSION: str = "v21.0"
    # Nome do template aprovado na Meta Business Manager para o relatório
    # semanal — mensagens iniciadas pela empresa (fora da janela de 24h de
    # atendimento) SÓ podem ser enviadas via template pré-aprovado, nunca
    # como texto livre. Ver DECISÃO em app/services/whatsapp_client.py.
    WHATSAPP_REPORT_TEMPLATE_NAME: str = "weekly_report"

    # --- Cadastro público (self-signup) ---
    # URL base do FRONTEND (não desta API) — usada só para montar o link
    # de recuperação de senha dentro do e-mail (ex: "https://app.insighta
    # rcm.com/reset-password?token=..."). Sem isso configurado, o link
    # cai no fallback abaixo (localhost), o que é aceitável em dev mas
    # nunca deveria acontecer em produção — documentar aqui é o suficiente
    # por ora (não falha o boot: mesma filosofia de feature opcional que
    # degrada, não quebra).
    FRONTEND_BASE_URL: str = "http://localhost:5173"
    # Validade do token de "esqueci minha senha" — curta de propósito
    # (é um link enviado por e-mail, canal fora do controle do produto;
    # quanto mais tempo válido, maior a janela de uso indevido se o
    # e-mail for interceptado).
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 30

    # --- Login com Google ("Sign in with Google") — OPCIONAL ---
    # Client ID do OAuth 2.0 criado no Google Cloud Console (APIs &
    # Services > Credentials > OAuth client ID > Web application) — é
    # PÚBLICO por natureza (vai no HTML do frontend), nunca um segredo.
    # Sem client_secret nenhum: o fluxo usa Google Identity Services
    # (https://accounts.google.com/gsi/client), que devolve ao frontend um
    # ID token JÁ ASSINADO pelo Google — este backend só VERIFICA a
    # assinatura e a claim "aud" contra este client_id (ver
    # app/services/google_oauth_client.py), nunca troca código de
    # autorização por token. Sem GOOGLE_OAUTH_CLIENT_ID configurado,
    # POST /auth/google devolve 503 — mesma degradação graciosa de
    # SMTP_HOST/SENTRY_DSN ausentes.
    GOOGLE_OAUTH_CLIENT_ID: str | None = None

    # --- E-mail transacional (recuperação de senha) — OPCIONAL ---
    # DECISÃO — SMTP genérico, não um SDK de provedor específico
    # -------------------------------------------------------------------
    # Sem SMTP_HOST configurado, EmailClient (app/services/email_client.py)
    # não falha o boot nem a requisição — só REGISTRA em log o e-mail que
    # teria sido enviado (mesmo padrão de degradação graciosa de
    # SENTRY_DSN/ANTHROPIC_API_KEY acima: feature ausente vira "desligada",
    # nunca "quebrada"). Isso permite terminar e testar o FLUXO inteiro
    # (cadastro, geração de token, endpoint de confirmação) antes mesmo de
    # decidir qual provedor de e-mail contratar — bastando preencher estas
    # variáveis depois (compatível com SendGrid, Mailgun, AWS SES,
    # Postmark, Gmail/Workspace... qualquer um que ofereça credenciais SMTP).
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    # Remetente exibido ao destinatário — a maioria dos provedores exige
    # que este endereço esteja verificado na conta do provedor.
    SMTP_FROM_EMAIL: str = "no-reply@insighta-rcm.com"
    SMTP_FROM_NAME: str = "Insighta RCM"
    SMTP_USE_TLS: bool = True

    # --- Sentry (monitoramento de erros — opcional) ---
    # Sem SENTRY_DSN, nada é inicializado: zero overhead, zero mudança de
    # comportamento (mesmo padrão de ANTHROPIC_API_KEY/AWS_S3_CONTRACTS_BUCKET
    # acima — feature degrada para "desligada", nunca derruba o boot).
    # Com DSN configurado, erro não tratado da API (app/main.py) e do
    # worker de ingestão (app/worker/ingestion_worker.py) são reportados
    # automaticamente, com o request_id como tag para correlacionar com o
    # log estruturado.
    SENTRY_DSN: str | None = None
    # Amostragem de performance tracing — desligada por padrão (0.0) porque
    # tracing consome quota do plano Sentry rapidamente; é uma decisão
    # explícita do operador ligar, não um padrão "grátis" que pode
    # estourar o free tier sem ninguém perceber.
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0
    # Amostragem de profiling — mesma lógica do tracing acima, desligada
    # por padrão.
    SENTRY_PROFILES_SAMPLE_RATE: float = 0.0


@lru_cache
def get_settings() -> Settings:
    # lru_cache faz a Settings ser lida do ambiente uma única vez por processo,
    # evitando re-parsear variáveis de ambiente a cada request.
    return Settings()
