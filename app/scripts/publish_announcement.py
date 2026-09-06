"""
app/scripts/publish_announcement.py

Publica uma novidade na Central de Notificações (o sino no topo do
produto) — direto no banco, porque não existe (ainda) um "super-admin"
com sessão HTTP própria capaz de escrever em
`core.platform_announcements` (a única tabela sem tenant_id/RLS deste
schema — ver DECISÃO em app/sql/023_announcements_and_support.sql).
Quem publica é a equipe que opera a plataforma, não um cliente.

Toda linha nasce publicada (sem rascunho) — revise o texto ANTES de
rodar o comando.

COMO RODAR NO RAILWAY
-----------------------------------------------------------------------
    railway link
    railway run python -m app.scripts.publish_announcement \\
        --title "Documento de recurso de glosa automático" \\
        --body "Agora cada recurso de glosa já sai com o PDF pronto, com todos os dados do caso preenchidos."

Localmente, com DATABASE_ADMIN_URL configurada no ambiente, roda igual
sem o `railway run`.

IDEMPOTÊNCIA
-----------------------------------------------------------------------
Cada execução cria uma linha NOVA (não há upsert por título) — rodar
duas vezes com o mesmo texto duplica a novidade. Não é um problema
prático (é um comando manual, executado uma vez por release), mas vale
saber antes de copiar/colar o comando de novo por engano.
"""
import argparse
import asyncio
import os
import sys
import uuid

import asyncpg

from app.scripts.bootstrap_db import _to_asyncpg_dsn


async def _publish(dsn: str, *, title: str, body: str) -> None:
    conn = await asyncpg.connect(dsn=dsn)
    try:
        announcement_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO core.platform_announcements (id, title, body) VALUES ($1, $2, $3)",
            announcement_id,
            title,
            body,
        )
        print(f"Novidade publicada: '{title}' (id={announcement_id}) — já visível para todo tenant.")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Publica uma novidade na Central de Notificações (sino).")
    parser.add_argument("--title", required=True, help="Título curto (até 200 caracteres).")
    parser.add_argument("--body", required=True, help="Texto da novidade, em linguagem de produto — não changelog técnico.")
    args = parser.parse_args()

    admin_dsn = os.environ.get("DATABASE_ADMIN_URL")
    if not admin_dsn:
        print("Variável de ambiente DATABASE_ADMIN_URL não encontrada neste ambiente.", file=sys.stderr)
        sys.exit(1)

    asyncio.run(_publish(_to_asyncpg_dsn(admin_dsn), title=args.title, body=args.body))


if __name__ == "__main__":
    main()
