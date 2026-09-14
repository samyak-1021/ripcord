"""Small operator CLI: ``python -m ripcord.cli <command>``.

Two commands, both solving the bootstrap problem — you need a credential to
create a credential:

``mint-bootstrap``
    Print a key suitable for ``BOOTSTRAP_ADMIN_KEY``. Touches no database, so it
    works before the service has ever run.

``create-key``
    Write a real key straight into the database, for when you have DB access but
    no running API (first deploy, disaster recovery, seeding CI).
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from ripcord import auth
from ripcord.db import SessionFactory
from ripcord.models import ApiKey


def _mint_bootstrap() -> int:
    full_key, _, _ = auth.generate_key()
    print("Add this to your environment (or .env):\n")
    print(f"    BOOTSTRAP_ADMIN_KEY={full_key}\n")
    print("Then mint a real admin key against the running API:\n")
    print(
        '    curl -X POST localhost:8000/keys -H "Authorization: Bearer '
        f'{full_key}" \\\n'
        '         -H "Content-Type: application/json" \\\n'
        '         -d \'{"name":"admin","scopes":["admin"]}\'\n'
    )
    print("...and unset BOOTSTRAP_ADMIN_KEY once you have.")
    return 0


async def _create_key(name: str, scopes: list[str]) -> int:
    unknown = sorted(set(scopes) - auth.ALL_SCOPES)
    if unknown:
        print(f"error: unknown scope(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"valid: {', '.join(sorted(auth.ALL_SCOPES))}", file=sys.stderr)
        return 2

    full_key, key_id, secret_hash = auth.generate_key()
    async with SessionFactory() as session:
        session.add(
            ApiKey(key_id=key_id, secret_hash=secret_hash, name=name, scopes=scopes)
        )
        await session.commit()

    print(f"Created key '{name}' ({key_id}) with scopes: {', '.join(scopes)}\n")
    print(f"    {full_key}\n")
    print("This is the only time the key is shown. Store it now.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ripcord.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("mint-bootstrap", help="Print a BOOTSTRAP_ADMIN_KEY value")

    create = sub.add_parser("create-key", help="Create a key in the database")
    create.add_argument("--name", required=True, help="Label, e.g. 'ci-deploy'")
    create.add_argument(
        "--scopes",
        required=True,
        help=f"Comma-separated. Valid: {','.join(sorted(auth.ALL_SCOPES))}",
    )

    args = parser.parse_args(argv)
    if args.command == "mint-bootstrap":
        return _mint_bootstrap()
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    return asyncio.run(_create_key(args.name, scopes))


if __name__ == "__main__":
    raise SystemExit(main())
