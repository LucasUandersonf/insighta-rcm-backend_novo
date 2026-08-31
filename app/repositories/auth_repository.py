"""
app/repositories/auth_repository.py

Camada de acesso a dados exclusiva para o fluxo de autenticação. Isolada
dos demais repositórios (que sempre operam sob uma sessão tenant-aware)
porque este aqui é o ÚNICO lugar do código que legitimamente consulta
dados cross-tenant, através da função SQL SECURITY DEFINER
`core.resolve_login` (ver app/sql/002_auth_resolver.sql).

CORREÇÃO (Auditoria Go-Live, achado F-04): `resolve_login` devolvia só o
PRIMEIRO registro encontrado para um e-mail, mesmo quando o mesmo e-mail
existe em mais de um tenant (consultor multi-clínica) — a escolha de
qual tenant era arbitrária, não uma decisão de negócio. Agora devolve
TODOS os candidatos; quem decide o que fazer com mais de um é o endpoint
de login (app/api/v1/endpoints/auth.py), não esta camada.
"""
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class LoginRecord:
    user_id: str
    tenant_id: str
    hashed_password: str
    role: str
    is_active: bool
    tenant_is_active: bool
    tenant_trade_name: str


class AuthRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def resolve_login_candidates(self, email: str) -> list[LoginRecord]:
        result = await self.session.execute(
            text("SELECT * FROM core.resolve_login(:email)"),
            {"email": email},
        )
        return [
            LoginRecord(
                user_id=str(row["user_id"]),
                tenant_id=str(row["tenant_id"]),
                hashed_password=row["hashed_password"],
                role=row["role"],
                is_active=row["is_active"],
                tenant_is_active=row["tenant_is_active"],
                tenant_trade_name=row["tenant_trade_name"],
            )
            for row in result.mappings().all()
        ]
