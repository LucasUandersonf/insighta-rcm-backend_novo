"""
Importar todos os models aqui garante que Base.metadata "conheça" todas
as tabelas no momento em que o Alembic (ou qualquer código) importar
`app.models`. Sem isso, um model que só é importado meio-indiretamente
por um endpoint específico poderia ficar de fora do autogenerate.
"""
from app.models.announcement import Announcement  # noqa: F401
from app.models.announcement_read import AnnouncementRead  # noqa: F401
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
from app.models.password_reset_token import PasswordResetToken  # noqa: F401
from app.models.patient import Patient  # noqa: F401
from app.models.platform_audit_log import PlatformAuditLog  # noqa: F401
from app.models.platform_risk_alert import PlatformRiskAlert  # noqa: F401
from app.models.platform_user import PlatformUser  # noqa: F401
from app.models.professional import Professional  # noqa: F401
from app.models.professional_availability import ProfessionalAvailability  # noqa: F401
from app.models.report_recipient import ReportRecipient  # noqa: F401
from app.models.support_request import SupportRequest  # noqa: F401
from app.models.tenant import Tenant  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.webhook_delivery_queue import WebhookDeliveryQueueEntry  # noqa: F401
from app.models.webhook_subscription import WebhookSubscription  # noqa: F401
