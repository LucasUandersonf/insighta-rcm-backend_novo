"""
Importar todos os models aqui garante que Base.metadata "conheça" todas
as tabelas no momento em que o Alembic (ou qualquer código) importar
`app.models`. Sem isso, um model que só é importado meio-indiretamente
por um endpoint específico poderia ficar de fora do autogenerate.
"""
from app.models.api_key import ApiKey  # noqa: F401
from app.models.appointment import Appointment  # noqa: F401
from app.models.audit_log import AuditLog  # noqa: F401
from app.models.billing import Billing  # noqa: F401
from app.models.contract import Contract  # noqa: F401
from app.models.contract_item import ContractItem  # noqa: F401
from app.models.denial_appeal import DenialAppeal, DenialAppealAttachment  # noqa: F401
from app.models.ingestion_file import IngestionFile  # noqa: F401
from app.models.ingestion_raw_row import IngestionRawRow  # noqa: F401
from app.models.insurance_company import InsuranceCompany  # noqa: F401
from app.models.insurance_plan import InsurancePlan  # noqa: F401
from app.models.insurance_plan_alias import InsurancePlanAlias  # noqa: F401
from app.models.marketing_spend import MarketingSpend  # noqa: F401
from app.models.marketing_webhook_event import MarketingWebhookEvent  # noqa: F401
from app.models.patient import Patient  # noqa: F401
from app.models.professional import Professional  # noqa: F401
from app.models.professional_availability import ProfessionalAvailability  # noqa: F401
from app.models.report_recipient import ReportRecipient  # noqa: F401
from app.models.tenant import Tenant  # noqa: F401
from app.models.user import User  # noqa: F401
