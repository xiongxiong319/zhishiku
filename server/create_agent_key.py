#!/usr/bin/env python3
"""Provision a dedicated LeafWiki user and API key for one agent."""

import argparse
import datetime as dt
import http.cookiejar
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path


def die(message):
    print(message, file=sys.stderr)
    sys.exit(1)


class Client:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def request(self, method, path, body=None, headers=None):
        url = self.base_url + path
        data = None
        request_headers = {"Accept": "application/json"}
        if headers:
            request_headers.update(headers)
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url, data=data, method=method, headers=request_headers
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read().decode("utf-8", "replace")
                status = response.status
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", "replace")
            print(f"HTTP {error.code}: {raw}", file=sys.stderr)
            sys.exit(1)
        except urllib.error.URLError as error:
            die(f"Could not reach LeafWiki: {error.reason}")

        if not raw:
            return status, None
        try:
            return status, json.loads(raw)
        except json.JSONDecodeError:
            return status, raw

    def csrf_token(self):
        names = {"__Host-leafwiki_csrf", "leafwiki_csrf"}
        for cookie in self.jar:
            if cookie.name in names:
                return cookie.value
        die("CSRF cookie missing; check HTTPS and Nginx X-Forwarded-Proto")


def ensure_user(client, username, email, role):
    _, users = client.request("GET", "/api/users")
    if not isinstance(users, list):
        die(f"Unexpected user list response: {users!r}")

    for user in users:
        if user.get("username") == username:
            if user.get("role") != role:
                die(
                    f"User {username!r} already exists with role {user.get('role')!r}; "
                    f"requested role is {role!r}"
                )
            return user, False

    password = secrets.token_hex(24)
    _, created = client.request(
        "POST",
        "/api/users",
        {
            "username": username,
            "email": email,
            "password": password,
            "role": role,
        },
        {"X-CSRF-Token": client.csrf_token()},
    )
    if not isinstance(created, dict) or not created.get("id"):
        die(f"Unexpected create-user response: {created!r}")
    return created, True


def main():
    parser = argparse.ArgumentParser(
        description="Create a dedicated LeafWiki agent user and API key"
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("LEAFWIKI_BASE_URL", "https://wiki.kkuu.de"),
    )
    parser.add_argument("--agent-username", required=True)
    parser.add_argument("--agent-email", required=True)
    parser.add_argument("--role", choices=["viewer", "editor"], default="viewer")
    parser.add_argument("--key-name")
    parser.add_argument("--expires-days", type=int, default=90)
    parser.add_argument("--output-file")
    args = parser.parse_args()

    admin_username = os.environ.get("LEAFWIKI_ADMIN_USERNAME", "").strip()
    admin_password = os.environ.get("LEAFWIKI_ADMIN_PASSWORD", "")
    if not admin_username or not admin_password:
        die("set LEAFWIKI_ADMIN_USERNAME and LEAFWIKI_ADMIN_PASSWORD")

    client = Client(args.base_url)
    _, config = client.request("GET", "/api/config")
    if not isinstance(config, dict) or not config.get("enableApiKeyManagement"):
        die(
            "API key management is disabled. Set "
            "LEAFWIKI_ENABLE_API_KEY_MANAGEMENT=true on LeafWiki, recreate the "
            "container, then retry."
        )

    _, login = client.request(
        "POST",
        "/api/auth/login",
        {"identifier": admin_username, "password": admin_password},
    )
    if not isinstance(login, dict) or not login.get("user"):
        die(f"Login failed: {login!r}")
    if login["user"].get("role") != "admin":
        die("The supplied account is not an admin")

    user, user_created = ensure_user(
        client, args.agent_username, args.agent_email, args.role
    )

    expires_at = None
    if args.expires_days > 0:
        expires_at = (
            dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=args.expires_days)
        ).isoformat().replace("+00:00", "Z")

    key_name = args.key_name or f"{args.agent_username}-key"
    _, created_key = client.request(
        "POST",
        "/api/api-keys",
        {
            "name": key_name,
            "userId": user["id"],
            "role": args.role,
            "expiresAt": expires_at,
        },
        {"X-CSRF-Token": client.csrf_token()},
    )
    if not isinstance(created_key, dict) or not created_key.get("secret"):
        die(f"Unexpected create-key response: {created_key!r}")

    result = {
        "baseUrl": args.base_url.rstrip("/"),
        "agentUsername": args.agent_username,
        "agentEmail": args.agent_email,
        "role": args.role,
        "memoryRoot": f"agents/{args.agent_username}/",
        "memoryPolicyVersion": "1.0",
        "keyName": key_name,
        "expiresAt": expires_at,
        "userCreated": user_created,
        "apiKeyId": created_key["key"]["id"],
        "apiKey": created_key["secret"],
    }

    if args.output_file:
        path = Path(args.output_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        print(f"wrote credentials to {path}")
        print("The API key is shown only once; store this file securely.")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("The API key is shown only once; store it securely.")


if __name__ == "__main__":
    main()
