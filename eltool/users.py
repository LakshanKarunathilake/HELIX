"""Operator CLI for accounts: ``python -m eltool.users create-admin --email E [--name N]``.

The admin is created active with no Google sign-in linked; the link is made
at that person's first sign-in.
"""
from __future__ import annotations

import argparse
import sys

from eltool import userdb


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eltool.users")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-admin", help="create the first (or another) administrator")
    p.add_argument("--email", required=True)
    p.add_argument("--name", help="display name (default: the part of the email before @)")
    args = parser.parse_args(argv)

    email = args.email.strip()
    name = args.name or email.split("@")[0]
    if userdb.get_user_by_email(email) is not None:
        print(f"A user with email {email.lower()} already exists.", file=sys.stderr)
        return 1
    try:
        user = userdb.create_user(email, name, "admin")
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"Created admin {user.email} (id {user.id}). They are linked at their first Google sign-in.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
