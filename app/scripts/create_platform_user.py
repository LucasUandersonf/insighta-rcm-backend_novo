"""
app/scripts/create_platform_user.py

Cria ou reseta a senha de uma conta individual do painel interno de
Customer Success (`core.platform_users` — ver DECISÃO completa em
app/sql/029_platform_users.sql) — direto no banco, porque não existe
self-signup para a equipe da própria Insighta (mesmo raciocínio de
publish_announcement.py: quem opera a plataforma não é um cliente).

COMO RODAR NO RAILWAY
-----------------------------------------------------------------------
    railway link
    railway run python -m app.scripts.create_platform_user \\
        --email ana@insighta-rcm.com --full-name "Ana Souza"

Sem --password, gera uma senha temporária aleatória e a imprime uma
única vez no terminal — copie AGORA, ela nunca é reexibida (mesmo
princípio de generate_temporary_password() em app/core/security.py).

Localmente, com DATABASE_ADMIN_URL configurada no ambiente, roda igual
sem o `railway run`.

IDEMPOTÊNCIA
-----------------------------------------------------------------------
Rodar de novo com o MESMO --email atualiza full_name/senha da conta
existente (upsert por e-mail) em vez de duplicar — é também como se
reseta a senha de alguém que esqueceu.
"""
import argparse
import asyncio
import os
import sys
import uuid

import asyncpg

from app.core.security import generate_temporary_password, hash_password
from app.scripts.bootstrap_db import _to_asyncpg_dsn


async def _upsert(dsn: str, *, email: str, full_name: str, password: str) -> None:
    conn = await asyncpg.connect(dsn=dsn)
    try:
        hashed = hash_password(password)
        row = await conn.fetchrow(
            """
            INSERT INTO core.platform_users (id, email, hashed_password, full_name, is_active)
            VALUES ($1, $2, $3, $4, true)
            ON CONFLICT (email) DO UPDATE
                SET hashed_password = EXCLUDED.hashed_password,
                    full_name = EXCLUDED.full_name,
                    is_active = true
            RETURNING id, (xmax = 0) AS inserted
            """,
            uuid.uuid4(),
            email,
            hashed,
            full_name,
        )
        action = "criada" if row["inserted"] else "atualizada (senha resetada)"
        print(f"Conta {action}: {email} (id={row['id']}).")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Cria ou reseta a senha de uma conta do painel interno de Customer Success.")
    parser.add_argument("--email", required=True, help="E-mail da pessoa da equipe Insighta.")
    parser.add_argument("--full-name", required=True, help="Nome completo, para exibição no histórico de ações.")
    parser.add_argument("--password", help="Senha em texto puro. Se omitido, gera uma senha temporária aleatória.")
    args = parser.parse_args()

    admin_dsn = os.environ.get("DATABASE_ADMIN_URL")
    if not admin_dsn:
        print("Variável de ambiente DATABASE_ADMIN_URL não encontrada neste ambiente.", file=sys.stderr)
        sys.exit(1)

    password = args.password or generate_temporary_password()
    asyncio.run(_upsert(_to_asyncpg_dsn(admin_dsn), email=args.email, full_name=args.full_name, password=password))
    if not args.password:
        print(f"Senha temporária gerada (copie agora, não será reexibida): {password}")


if __name__ == "__main__":
    main()
