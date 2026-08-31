import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Mesmo enum de app/models: name deve bater com o tipo core.user_role já
# criado pelo DDL (create_type=False evita o Alembic tentar recriar o
# ENUM que já existe no banco).
UserRoleEnum = Enum(
    "owner", "admin", "financeiro", "atendimento", "auditor",
    name="user_role", schema="core", create_type=False,
)


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core.tenants.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(UserRoleEnum, nullable=False, default="atendimento")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Ver 006_platform_admin.sql: true logo após criação/reset pelo
    # admin/owner — força troca de senha no primeiro acesso, mesmo sem
    # provedor de e-mail transacional integrado ainda.
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    password_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
