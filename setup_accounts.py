"""Create/reset local accounts interactively; passwords never enter arguments."""
from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import subprocess
import sys

from account_store import create_user, list_users, normalize_username, password_bytes, reset_password

ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / ".runtime" / "sandbox-agent" / "app.db"


def prepare_storage() -> None:
    if os.name != "nt":
        raise RuntimeError("This server requires Windows native filesystem protections.")
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    if runtime.is_symlink() or runtime.stat().st_file_attributes & 0x400:
        raise RuntimeError("The private runtime directory must not be a filesystem link.")
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts/protect-runtime.ps1")],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW, check=False,
    )
    if result.returncode:
        raise RuntimeError("Could not protect .runtime permissions; account setup stopped.")
    for path in (DATABASE.parent, DATABASE.parent / "uploads", ROOT / "agent"):
        path.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or path.stat().st_file_attributes & 0x400:
            raise RuntimeError("Account storage and agent directories must not be filesystem links.")
    if DATABASE.exists() and (DATABASE.is_symlink() or DATABASE.stat().st_file_attributes & 0x400):
        raise RuntimeError("The account database must not be a filesystem link.")


def prompt_password() -> str:
    if not sys.stdin.isatty():
        raise RuntimeError("Run account setup in an interactive terminal; passwords are not accepted through command-line arguments or redirected input.")
    first = getpass.getpass("Password (12+ characters, max 72 UTF-8 bytes): ")
    password_bytes(first)
    if first != getpass.getpass("Repeat password: "):
        raise ValueError("Passwords did not match; no account was changed.")
    return first


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="create a local account; prompts for its password")
    create.add_argument("username", nargs="?", help="optional ID; otherwise prompted")
    reset = commands.add_parser("reset-password", help="reset a password and revoke this account's sessions")
    reset.add_argument("username")
    commands.add_parser("list", help="list local usernames without hashes or session data")
    args = parser.parse_args()
    try:
        if args.command == "list":
            for username in list_users(DATABASE):
                print(username)
            return 0
        username = normalize_username(args.username or input("Username: "))
        password = prompt_password()
        prepare_storage()
        if args.command == "create":
            username = create_user(DATABASE, username, password)
            print(f"Created account: {username}")
        else:
            username = reset_password(DATABASE, username, password)
            print(f"Password reset and existing sessions revoked: {username}")
        return 0
    except (ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("Account setup cancelled.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
