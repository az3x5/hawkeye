"""Token administration.

Credentials are issued deliberately, by an operator, and the secret is printed
exactly once. There is no endpoint for this on purpose: an API that can mint
its own credentials is an API that can escalate its own privileges.

    python -m app.tokens issue --subject alice@example.com --kind user --scope review
    python -m app.tokens list
    python -m app.tokens revoke <token-uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from uuid import UUID

from app.connectors.postgres import PostgresConnector
from app.connectors.postgres.tokens import SqlAlchemyTokenStore
from app.core.config import get_settings
from app.domain.auth import Scope, new_token


async def _issue(subject: str, kind: str, scopes: list[Scope], lifetime_days: int | None) -> int:
    record, secret = new_token(subject, kind, scopes, lifetime_days=lifetime_days)
    connector = PostgresConnector(str(get_settings().postgres_dsn))
    try:
        async with connector.session() as session:
            await SqlAlchemyTokenStore(session).add(record)
    finally:
        await connector.close()

    print(f"token_uuid : {record.token_uuid}")
    print(f"subject    : {record.subject} ({record.kind})")
    print(f"scopes     : {', '.join(sorted(s.value for s in record.scopes))}")
    print(f"expires    : {record.expires_at.isoformat() if record.expires_at else 'never'}")
    print(f"secret     : {secret}")
    print("\nStore the secret now: it is not recoverable, only its hash is kept.")
    if record.expires_at is None:
        print(
            "WARNING: this credential never expires. It stays valid until somebody "
            "notices it has leaked."
        )
    return 0


async def _list() -> int:
    connector = PostgresConnector(str(get_settings().postgres_dsn))
    try:
        async with connector.session() as session:
            tokens = await SqlAlchemyTokenStore(session).list_all()
    finally:
        await connector.close()

    if not tokens:
        print("no credentials have been issued")
        return 0
    for token in tokens:
        state = "active" if token.usable() else ("revoked" if not token.active else "expired")
        scopes = ", ".join(sorted(s.value for s in token.scopes))
        expires = token.expires_at.date().isoformat() if token.expires_at else "never"
        print(
            f"{token.token_uuid}  {state:8}  {token.kind:7}  {token.subject:28}  "
            f"{expires:10}  {scopes}"
        )
    return 0


async def _revoke(token_uuid: UUID) -> int:
    connector = PostgresConnector(str(get_settings().postgres_dsn))
    try:
        async with connector.session() as session:
            changed = await SqlAlchemyTokenStore(session).disable(token_uuid)
    finally:
        await connector.close()

    print(f"revoked {token_uuid}" if changed else f"{token_uuid} is unknown or already revoked")
    return 0 if changed else 1


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the requested command."""
    parser = argparse.ArgumentParser(prog="app.tokens", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    issue = commands.add_parser("issue", help="mint a credential")
    issue.add_argument("--subject", required=True, help="who or what this credential is for")
    issue.add_argument("--kind", choices=["user", "service"], default="user")
    issue.add_argument(
        "--scope",
        action="append",
        required=True,
        choices=[s.value for s in Scope],
        help="may be repeated",
    )
    lifetime = issue.add_mutually_exclusive_group()
    lifetime.add_argument(
        "--expires-in-days",
        type=int,
        help="credential lifetime; defaults to FACEID_TOKEN_LIFETIME_DAYS",
    )
    lifetime.add_argument(
        "--never-expires",
        action="store_true",
        help="mint a credential with no end date; must be asked for explicitly",
    )

    commands.add_parser("list", help="list issued credentials")

    revoke = commands.add_parser("revoke", help="revoke a credential")
    revoke.add_argument("token_uuid", type=UUID)

    args = parser.parse_args(argv)
    if args.command == "issue":
        lifetime_days = (
            None
            if args.never_expires
            else (args.expires_in_days or get_settings().token_lifetime_days)
        )
        return asyncio.run(
            _issue(args.subject, args.kind, [Scope(s) for s in args.scope], lifetime_days)
        )
    if args.command == "list":
        return asyncio.run(_list())
    return asyncio.run(_revoke(args.token_uuid))


if __name__ == "__main__":
    sys.exit(main())
