"""
app/scripts/create_admin.py

Script de UMA VEZ SÓ para criar (ou resetar a senha de) o primeiro usuário
administrador do sistema, direto no banco de produção — necessário porque
não existe tela de "cadastro público" (por design: toda conta nova é
criada por um owner/admin já autenticado, via Gestão de Usuários). No
dia zero, ninguém está autenticado ainda, então esse primeiro usuário
precisa entrar por fora da aplicação.

COMO RODAR NO RAILWAY
-----------------------------------------------------------------------
Este script roda dentro do MESMO ambiente do backend (mesmas variáveis
de ambiente, mesmas dependências já instaladas — passlib/argon2 incluso),
então a forma mais simples é via Railway CLI, que injeta as variáveis do
serviço automaticamente:

    railway link                     # uma vez, escolhe o projeto/serviço do backend
    railway run python -m app.scripts.create_admin \\
        --email "voce@suaclinica.com" \\
        --password "uma-senha-forte-aqui" \\
        --full-name "Seu Nome" \\
        --tenant-cnpj "00.000.000/0001-00" \\
        --tenant-legal-name "Sua Clínica LTDA" \\
        --tenant-trade-name "Sua Clínica"

Se sua clínica (tenant) JÁ existe no banco (ex: você já tinha cadastrado
antes), pode omitir --tenant-legal-name/--tenant-trade-name — o script
reconhece o tenant existente só pelo CNPJ e cria o usuário nele.

CONSOLIDAÇÃO MULTI-UNIDADE (Épico F3.2)
-----------------------------------------------------------------------
Pra agrupar duas ou mais clínicas do MESMO dono num dashboard
consolidado (GET /analytics/organization-summary), rode este script uma
vez por unidade passando o MESMO --organization-name (a organização é
criada na primeira chamada, reaproveitada nas seguintes — idempotente,
mesmo raciocínio do CNPJ). Continua sendo um agrupamento feito por ops,
não uma tela de "criar organização" dentro do produto.

IDEMPOTÊNCIA
-----------------------------------------------------------------------
Rodar de novo com o mesmo e-mail/CNPJ não duplica nada: se o usuário já
existir nesse tenant, o script apenas ATUALIZA a senha/nome/papel dele
(útil para resetar a própria senha caso esqueça). Nunca cria dois
usuários nem duas clínicas com o mesmo CNPJ.

SEGURANÇA
-----------------------------------------------------------------------
A senha é passada por argumento de linha de comando por simplicidade
(ambiente de bootstrap único, não repetido) — o hash real (argon2) é
gerado com a MESMA função que o resto do sistema usa
(app.core.security.hash_password), garantindo que o login funcione
depois exatamente como qualquer outro usuário criado pela UI.
"""
import argparse
import asyncio
import os
import sys
import uuid

import asyncpg

# Reaproveita a mesma conversão de DSN já usada pelo bootstrap principal
# (DATABASE_ADMIN_URL vem como postgresql:// ou postgresql+asyncpg://).
from app.scripts.bootstrap_db import _to_asyncpg_dsn
from app.core.security import hash_password

_VALID_ROLES = ("owner", "admin", "financeiro", "atendimento", "auditor")


async def _resolve_organization_id(conn: asyncpg.Connection, organization_name: str | None) -> uuid.UUID | None:
    """
    Épico F3.2 do Plano Diretor ("Consolidação multi-unidade") —
    agrupar tenants numa Organization é a MESMA categoria de decisão que
    criar o próprio tenant (ver docstring do módulo): uma relação
    comercial multi-unidade, atribuída por ops via este script, não uma
    tela de "criar organização" self-service dentro do produto.

    Reconhece a Organization pelo NOME (case-sensitive) — reaproveita se
    já existir (idempotente, mesmo padrão do tenant por CNPJ acima),
    cria se não existir. `None` (flag omitida) devolve `None`, deixando
    o tenant sem organização — o estado normal da maioria das clínicas.
    """
    if organization_name is None:
        return None
    org_row = await conn.fetchrow("SELECT id FROM core.organizations WHERE name = $1", organization_name)
    if org_row is not None:
        print(f"Usando organização já existente: {organization_name} (id={org_row['id']}).")
        return org_row["id"]
    org_id = uuid.uuid4()
    await conn.execute("INSERT INTO core.organizations (id, name) VALUES ($1, $2)", org_id, organization_name)
    print(f"Organização criada: {organization_name} (id={org_id}).")
    return org_id


async def _create_or_update_admin(
    dsn: str,
    *,
    email: str,
    password: str,
    full_name: str,
    role: str,
    tenant_cnpj: str,
    tenant_legal_name: str | None,
    tenant_trade_name: str | None,
    organization_name: str | None,
) -> None:
    conn = await asyncpg.connect(dsn=dsn)
    try:
        organization_id = await _resolve_organization_id(conn, organization_name)

        tenant_row = await conn.fetchrow("SELECT id, trade_name FROM core.tenants WHERE cnpj = $1", tenant_cnpj)

        if tenant_row is None:
            if not tenant_legal_name or not tenant_trade_name:
                print(
                    f"Nenhuma clínica encontrada com o CNPJ '{tenant_cnpj}'. "
                    "Para criar uma nova, informe também --tenant-legal-name e --tenant-trade-name.",
                    file=sys.stderr,
                )
                sys.exit(1)
            tenant_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO core.tenants (id, legal_name, trade_name, cnpj, plan_tier, is_active, organization_id)
                VALUES ($1, $2, $3, $4, 'starter', true, $5)
                """,
                tenant_id,
                tenant_legal_name,
                tenant_trade_name,
                tenant_cnpj,
                organization_id,
            )
            print(f"Clínica criada: {tenant_trade_name} (id={tenant_id}).")
        else:
            tenant_id = tenant_row["id"]
            print(f"Usando clínica já existente: {tenant_row['trade_name']} (id={tenant_id}).")
            # Épico F3.2: --organization-name também atualiza uma clínica
            # JÁ existente (ex: agrupar duas unidades que já operavam
            # separadas) — omitir a flag deixa a organização atual
            # intocada (mesma convenção de "None = não alterar" do
            # PATCH /tenant, nunca desfaz um agrupamento sem pedir).
            if organization_id is not None:
                await conn.execute("UPDATE core.tenants SET organization_id = $1 WHERE id = $2", organization_id, tenant_id)
                print(f"Clínica {tenant_row['trade_name']} associada à organização '{organization_name}'.")

        hashed = hash_password(password)

        user_row = await conn.fetchrow(
            """
            INSERT INTO core.users (id, tenant_id, email, hashed_password, full_name, role, is_active, must_change_password)
            VALUES ($1, $2, $3, $4, $5, $6, true, false)
            ON CONFLICT (tenant_id, email) DO UPDATE SET
                hashed_password = EXCLUDED.hashed_password,
                full_name = EXCLUDED.full_name,
                role = EXCLUDED.role,
                is_active = true,
                must_change_password = false
            RETURNING id, (xmax = 0) AS inserted
            """,
            uuid.uuid4(),
            tenant_id,
            email,
            hashed,
            full_name,
            role,
        )

        action = "criado" if user_row["inserted"] else "atualizado (já existia)"
        print(f"Usuário {action}: {email} — papel: {role} — user_id={user_row['id']}.")
        print("Pronto — já pode fazer login no sistema com esse e-mail e senha.")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Cria ou atualiza um usuário administrador direto no banco.")
    parser.add_argument("--email", required=True, help="E-mail de login do usuário.")
    parser.add_argument("--password", required=True, help="Senha em texto puro (será hasheada com argon2 antes de salvar).")
    parser.add_argument("--full-name", required=True, help="Nome completo exibido no sistema.")
    parser.add_argument("--role", default="owner", choices=_VALID_ROLES, help="Papel de acesso (padrão: owner).")
    parser.add_argument("--tenant-cnpj", required=True, help="CNPJ da clínica — identifica um tenant já existente ou o novo a criar.")
    parser.add_argument("--tenant-legal-name", default=None, help="Razão social — só necessário se a clínica ainda não existir.")
    parser.add_argument("--tenant-trade-name", default=None, help="Nome fantasia — só necessário se a clínica ainda não existir.")
    parser.add_argument(
        "--organization-name",
        default=None,
        help=(
            "Épico F3.2 (Consolidação multi-unidade): agrupa esta clínica numa Organization para o dashboard "
            "consolidado (GET /analytics/organization-summary). Reaproveita a organização se já existir (pelo "
            "nome), cria se não existir. Omitir deixa a clínica avulsa (padrão) ou preserva a organização atual "
            "se ela já tinha uma."
        ),
    )
    args = parser.parse_args()

    admin_dsn = os.environ.get("DATABASE_ADMIN_URL")
    if not admin_dsn:
        print("Variável de ambiente DATABASE_ADMIN_URL não encontrada neste ambiente.", file=sys.stderr)
        sys.exit(1)

    asyncio.run(
        _create_or_update_admin(
            _to_asyncpg_dsn(admin_dsn),
            email=args.email,
            password=args.password,
            full_name=args.full_name,
            role=args.role,
            tenant_cnpj=args.tenant_cnpj,
            tenant_legal_name=args.tenant_legal_name,
            tenant_trade_name=args.tenant_trade_name,
            organization_name=args.organization_name,
        )
    )


if __name__ == "__main__":
    main()
