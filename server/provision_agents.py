#!/usr/bin/env python3
"""Provision multiple LeafWiki agent users and API keys from a JSON manifest."""

import argparse
import datetime as dt
import http.cookiejar
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


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
            raise ApiError(error.code, raw) from error
        except urllib.error.URLError as error:
            raise ApiError(0, f"could not reach LeafWiki: {error.reason}") from error

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
        raise ApiError(0, "CSRF cookie missing; check HTTPS and proxy headers")


def load_manifest(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("agents")
    if not isinstance(data, list) or not data:
        raise ValueError("manifest must be a non-empty list, or an object with an agents list")
    return data


def validate_spec(spec):
    if not isinstance(spec, dict):
        raise ValueError("each agent entry must be an object")
    username = str(spec.get("username", "")).strip()
    email = str(spec.get("email", "")).strip()
    role = str(spec.get("role", "viewer")).strip()
    if not username:
        raise ValueError("agent username is required")
    if not email:
        raise ValueError(f"agent {username!r} has no email")
    if role not in {"viewer", "editor"}:
        raise ValueError(f"agent {username!r} role must be viewer or editor")
    expires_days = int(spec.get("expiresDays", 90))
    if expires_days < 0:
        raise ValueError(f"agent {username!r} expiresDays must be >= 0")
    key_name = str(spec.get("keyName") or f"{username}-key").strip()
    return {
        "username": username,
        "email": email,
        "role": role,
        "keyName": key_name,
        "expiresDays": expires_days,
    }


def find_user(users, username):
    for user in users:
        if user.get("username") == username:
            return user
    return None


def ensure_user(client, spec):
    _, users = client.request("GET", "/api/users")
    if not isinstance(users, list):
        raise ApiError(0, f"unexpected user list response: {users!r}")

    user = find_user(users, spec["username"])
    if user:
        if user.get("role") != spec["role"]:
            raise ApiError(
                0,
                f"user exists with role {user.get('role')!r}, "
                f"requested {spec['role']!r}",
            )
        return user, False

    _, created = client.request(
        "POST",
        "/api/users",
        {
            "username": spec["username"],
            "email": spec["email"],
            "password": secrets.token_hex(24),
            "role": spec["role"],
        },
        {"X-CSRF-Token": client.csrf_token()},
    )
    if not isinstance(created, dict) or not created.get("id"):
        raise ApiError(0, f"unexpected create-user response: {created!r}")
    return created, True


def create_key(client, spec, user):
    expires_at = None
    if spec["expiresDays"] > 0:
        expires_at = (
            dt.datetime.now(dt.timezone.utc)
            + dt.timedelta(days=spec["expiresDays"])
        ).isoformat().replace("+00:00", "Z")

    _, created = client.request(
        "POST",
        "/api/api-keys",
        {
            "name": spec["keyName"],
            "userId": user["id"],
            "role": spec["role"],
            "expiresAt": expires_at,
        },
        {"X-CSRF-Token": client.csrf_token()},
    )
    if not isinstance(created, dict) or not created.get("secret"):
        raise ApiError(0, f"unexpected create-key response: {created!r}")
    return created, expires_at


def safe_name(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def main():
    parser = argparse.ArgumentParser(
        description="Provision multiple LeafWiki agents from a JSON manifest"
    )
    parser.add_argument("--config", required=True, help="path to agents.json")
    parser.add_argument(
        "--output-dir",
        default="/root/.leafwiki/agent-keys",
        help="directory for one JSON credential file per agent",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("LEAFWIKI_BASE_URL", "https://wiki.kkuu.de"),
    )
    args = parser.parse_args()

    admin_username = os.environ.get("LEAFWIKI_ADMIN_USERNAME", "").strip()
    admin_password = os.environ.get("LEAFWIKI_ADMIN_PASSWORD", "")
    if not admin_username or not admin_password:
        raise SystemExit("set LEAFWIKI_ADMIN_USERNAME and LEAFWIKI_ADMIN_PASSWORD")

    try:
        specs = [validate_spec(spec) for spec in load_manifest(args.config)]
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(str(error))

    client = Client(args.base_url)
    try:
        _, config = client.request("GET", "/api/config")
        if not isinstance(config, dict) or not config.get("enableApiKeyManagement"):
            raise SystemExit("API key management is disabled on this LeafWiki")

        _, login = client.request(
            "POST",
            "/api/auth/login",
            {"identifier": admin_username, "password": admin_password},
        )
        if not isinstance(login, dict) or not login.get("user"):
            raise SystemExit(f"login failed: {login!r}")
        if login["user"].get("role") != "admin":
            raise SystemExit("the supplied account is not an admin")

        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            output_dir.chmod(0o700)
        except OSError:
            pass

        errors = []
        for spec in specs:
            try:
                user, user_created = ensure_user(client, spec)
                created_key, expires_at = create_key(client, spec, user)
                record = {
                    "baseUrl": args.base_url.rstrip("/"),
                    "agentUsername": spec["username"],
                    "agentEmail": spec["email"],
                    "role": spec["role"],
                    "memoryRoot": f"agents/{spec['username']}/",
                    "memoryPolicyVersion": "1.0",
                    "keyName": spec["keyName"],
                    "expiresAt": expires_at,
                    "userCreated": user_created,
                    "apiKeyId": created_key["key"]["id"],
                    "apiKey": created_key["secret"],
                }
                target = output_dir / f"{safe_name(spec['username'])}.json"
                target.write_text(
                    json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                try:
                    target.chmod(0o600)
                except OSError:
                    pass
                print(
                    f"{spec['username']}: "
                    f"{'created user' if user_created else 'existing user'}, "
                    f"{spec['role']} key written to {target}"
                )
            except (ApiError, KeyError, TypeError) as error:
                errors.append(f"{spec['username']}: {error}")
                print(f"{spec['username']}: ERROR {error}", file=sys.stderr)

        print(f"\nprovisioned {len(specs) - len(errors)}/{len(specs)} agents")
        if errors:
            print("errors:", file=sys.stderr)
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
            raise SystemExit(1)
    except ApiError as error:
        raise SystemExit(f"API error {error.status}: {error}")


if __name__ == "__main__":
    main()
