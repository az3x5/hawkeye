"""Account administration.

    python -m app.users create --email admin@example.com --scope admin --scope review
    python -m app.users passwd --email admin@example.com
    python -m app.users list
    python -m app.users disable --email admin@example.com

The password is never a command-line argument: argv is visible to every process
on the host and lands in shell history. It is read from a prompt, or from stdin
when that is not a terminal, so scripted setup stays possible without leaking.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from app.connectors.postgres import PostgresConnector
from app.connectors.postgres.tokens import SqlAlchemyTokenStore
from app.connectors.postgres.users import SqlAlchemyUserStore
from app.core.config import get_settings
from app.domain.auth import Scope
from app.domain.repositories import ConflictError
from app.domain.users import (
    User,
    check_password_policy,
    hash_password,
    normalise_email,
)


def _read_password(confirm: bool) -> str:
    """Read a password from the terminal, or from a pipe when scripted."""
    if not sys.stdin.isatty():
        password = sys.stdin.readline().rstrip("\n")
        if not password:
            raise SystemExit("no password supplied on stdin")
        return password

    password = getpass.getpass("Password: ")
    if confirm and password != getpass.getpass("Repeat password: "):
        raise SystemExit("passwords did not match")
    return password


async def _create(email: str, scopes: list[Scope], password: str) -> int:
    normalised = normalise_email(email)
    check_password_policy(password, email=normalised)

    user = User(
        email=normalised,
        password_hash=hash_password(password),
        scopes=frozenset(scopes),
    )
    connector = PostgresConnector(str(get_settings().postgres_dsn))
    try:
        async with connector.session() as session:
            await SqlAlchemyUserStore(session).add(user)
    except ConflictError as exc:
        print(exc)
        return 1
    finally:
        await connector.close()

    print(f"created {user.email}")
    print(f"scopes  {', '.join(sorted(s.value for s in user.scopes))}")
    return 0


async def _passwd(email: str, password: str) -> int:
    normalised = normalise_email(email)
    check_password_policy(password, email=normalised)

    connector = PostgresConnector(str(get_settings().postgres_dsn))
    try:
        async with connector.session() as session:
            store = SqlAlchemyUserStore(session)
            user = await store.find_by_email(normalised)
            if user is None:
                print(f"no account for {normalised}")
                return 1
            await store.set_password(user.user_uuid, hash_password(password))
            # Changing a password must end the sessions the old one produced.
            revoked = await SqlAlchemyTokenStore(session).disable_for_user(user.user_uuid)
    finally:
        await connector.close()

    print(f"password changed for {normalised}; {revoked} session(s) revoked")
    return 0


async def _list() -> int:
    connector = PostgresConnector(str(get_settings().postgres_dsn))
    try:
        async with connector.session() as session:
            accounts = await SqlAlchemyUserStore(session).list_all()
    finally:
        await connector.close()

    if not accounts:
        print("no accounts")
        return 0
    for account in accounts:
        state = "active" if account.active else "disabled"
        last = account.last_login_at.date().isoformat() if account.last_login_at else "never"
        scopes = ", ".join(sorted(s.value for s in account.scopes))
        print(f"{account.email:32}  {state:8}  last login {last:10}  {scopes}")
    return 0


async def _set_disabled(email: str, disabled: bool) -> int:
    normalised = normalise_email(email)
    connector = PostgresConnector(str(get_settings().postgres_dsn))
    try:
        async with connector.session() as session:
            store = SqlAlchemyUserStore(session)
            user = await store.find_by_email(normalised)
            if user is None:
                print(f"no account for {normalised}")
                return 1
            await store.set_disabled(user.user_uuid, disabled)
            if disabled:
                await SqlAlchemyTokenStore(session).disable_for_user(user.user_uuid)
    finally:
        await connector.close()

    print(f"{'disabled' if disabled else 'enabled'} {normalised}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the requested command."""
    parser = argparse.ArgumentParser(prog="app.users", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="create an account")
    create.add_argument("--email", required=True)
    create.add_argument(
        "--scope",
        action="append",
        required=True,
        choices=[s.value for s in Scope],
        help="may be repeated",
    )

    passwd = commands.add_parser("passwd", help="change an account's password")
    passwd.add_argument("--email", required=True)

    commands.add_parser("list", help="list accounts")

    disable = commands.add_parser("disable", help="disable an account and its sessions")
    disable.add_argument("--email", required=True)

    enable = commands.add_parser("enable", help="re-enable an account")
    enable.add_argument("--email", required=True)

    args = parser.parse_args(argv)

    if args.command == "create":
        return asyncio.run(
            _create(args.email, [Scope(s) for s in args.scope], _read_password(confirm=True))
        )
    if args.command == "passwd":
        return asyncio.run(_passwd(args.email, _read_password(confirm=True)))
    if args.command == "list":
        return asyncio.run(_list())
    return asyncio.run(_set_disabled(args.email, disabled=args.command == "disable"))


if __name__ == "__main__":
    sys.exit(main())
