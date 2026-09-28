"""Local interactive setup for the desktop keyring account."""

import argparse
import getpass
import json
import logging
import sys

import httpx

from src.infra.http import GatewayError
from src.infra.http.auth import check_login
from src.infra.http.session import HEADERS, SessionStore


def account_cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["login", "disconnect"])
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    store = SessionStore()
    try:
        if args.action == "disconnect":
            store.disconnect()
            print('{"state": "disconnected"}')
            return 0
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            print("Login requires a terminal with hidden input.", file=sys.stderr)
            return 2
        username = getpass.getpass("Digikala account (hidden): ").strip()
        password = getpass.getpass("Password (hidden): ")
        if not username or not password:
            print("Account and password are required.", file=sys.stderr)
            return 2
        with httpx.Client(headers=HEADERS) as client:
            result = check_login(client, username, password)
            del username, password
            if result["state"] == "connected":
                store.connect(client.cookies)
                result["session_persisted"] = True
        print(json.dumps(result))
        return 0 if result["state"] == "connected" else 1
    except GatewayError as exc:
        print(json.dumps({"state": "not_connected", "reason": exc.error.code}))
        return 1
    except (KeyboardInterrupt, EOFError):
        print("Cancelled.", file=sys.stderr)
        return 1
