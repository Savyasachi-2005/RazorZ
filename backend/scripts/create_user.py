"""Create or update a RAZORZ console user.

    python -m scripts.create_user --email ops@razorz.local --role admin
    python -m scripts.create_user --email ops@razorz.local --reset-password

The password is read from a hidden prompt (or `RAZORZ_NEW_PASSWORD`) and is
never echoed, logged, or passed on the command line.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from app.auth.passwords import MIN_PASSWORD_LENGTH, PasswordPolicyError
from app.repositories.user_repository import ROLES, UserRepository


def _read_password() -> str:
    from_env = os.getenv("RAZORZ_NEW_PASSWORD")
    if from_env:
        return from_env
    first = getpass.getpass(f"Password (min {MIN_PASSWORD_LENGTH} chars): ")
    second = getpass.getpass("Confirm password: ")
    if first != second:
        print("Passwords do not match.", file=sys.stderr)
        raise SystemExit(2)
    return first


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or update a RAZORZ user")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--role", default="reviewer", choices=list(ROLES))
    parser.add_argument("--reset-password", action="store_true", help="update an existing user")
    args = parser.parse_args()

    users = UserRepository()
    password = _read_password()

    try:
        if args.reset_password:
            if not users.set_password(args.email, password):
                print(f"No user found for {args.email}", file=sys.stderr)
                raise SystemExit(1)
            print(f"Password updated for {args.email}")
            return
        created = users.create_user(
            email=args.email,
            password=password,
            full_name=args.name,
            role=args.role,
        )
    except PasswordPolicyError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None

    print(f"Created {created['email']} (role: {created['role']})")


if __name__ == "__main__":
    main()
