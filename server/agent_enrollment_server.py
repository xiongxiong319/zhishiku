#!/usr/bin/env python3
"""One-time-code enrollment service for LeafWiki agents."""

import datetime as dt
import hmac
import http.cookiejar
import ipaddress
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs


BASE_URL = os.environ.get("LEAFWIKI_BASE_URL", "https://wiki.kkuu.de").rstrip("/")
ADMIN_USERNAME = os.environ.get("LEAFWIKI_ADMIN_USERNAME", "").strip()
ADMIN_PASSWORD = os.environ.get("LEAFWIKI_ADMIN_PASSWORD", "")
BIND = os.environ.get("AGENT_ENROLL_BIND", "127.0.0.1")
PORT = int(os.environ.get("AGENT_ENROLL_PORT", "8787"))
CODES_FILE = Path(
    os.environ.get("AGENT_ENROLL_CODES_FILE", "/root/.leafwiki/enrollment-codes.json")
)
REGISTRY_DIR = Path(
    os.environ.get("AGENT_ENROLL_REGISTRY_DIR", "/root/.leafwiki/agent-keys")
)
MAX_BODY = int(os.environ.get("AGENT_ENROLL_MAX_BODY", "8192"))
TRUST_PROXY = os.environ.get("AGENT_ENROLL_TRUST_PROXY", "false").lower() == "true"
ALLOWED_NETWORKS = [
    ipaddress.ip_network(item.strip())
    for item in os.environ.get("AGENT_ENROLL_ALLOWED_IPS", "").split(",")
    if item.strip()
]
# 自助发 editor 码所需的共享令牌；为空则 editor 自助发码关闭（只允许 viewer）
EDITOR_TOKEN = os.environ.get("AGENT_ENROLL_EDITOR_TOKEN", "").strip()

CODES_LOCK = threading.Lock()
RATE_LOCK = threading.Lock()
ADMIN_LOCK = threading.Lock()
RATE_BY_IP = {}
GLOBAL_RATE = []
IP_LIMIT = int(os.environ.get("AGENT_ENROLL_IP_LIMIT", "10"))
GLOBAL_LIMIT = int(os.environ.get("AGENT_ENROLL_GLOBAL_LIMIT", "100"))
RATE_WINDOW_SECONDS = 3600
ADMIN_SESSION = None


class APIError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def iso_now():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def safe_name(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def load_codes():
    if not CODES_FILE.exists():
        return {"codes": []}
    data = json.loads(CODES_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("codes"), list):
        raise ValueError("codes file must contain a codes list")
    return data


def save_codes(data):
    CODES_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        CODES_FILE.parent.chmod(0o700)
    except OSError:
        pass
    temp = CODES_FILE.with_name(CODES_FILE.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        temp.chmod(0o600)
    except OSError:
        pass
    os.replace(temp, CODES_FILE)


def claim_code(code, agent_name):
    with CODES_LOCK:
        data = load_codes()
        for entry in data["codes"]:
            if hmac.compare_digest(str(entry.get("code", "")), code):
                if entry.get("used"):
                    raise APIError(409, "enrollment code already used")
                expires_at = entry.get("expiresAt")
                if expires_at and parse_iso(expires_at) <= dt.datetime.now(dt.timezone.utc):
                    raise APIError(410, "enrollment code expired")
                entry["used"] = True
                entry["usedBy"] = agent_name
                entry["usedAt"] = iso_now()
                save_codes(data)
                return dict(entry)
        raise APIError(401, "invalid enrollment code")


def release_code(code, agent_name):
    with CODES_LOCK:
        data = load_codes()
        for entry in data["codes"]:
            if hmac.compare_digest(str(entry.get("code", "")), code):
                if entry.get("used") and entry.get("usedBy") == agent_name:
                    entry["used"] = False
                    entry.pop("usedBy", None)
                    entry.pop("usedAt", None)
                    save_codes(data)
                return


def issue_self_service_code(role):
    """自助发一枚一次性注册码（7 天码 / 90 天 Key）。role 仅 viewer/editor。"""
    code = "lw_enroll_" + secrets.token_urlsafe(32)
    entry = {
        "code": code,
        "role": role,
        "expiresAt": (
            dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=7)
        ).isoformat().replace("+00:00", "Z"),
        "keyExpiresDays": 90,
        "used": False,
        "createdAt": dt.datetime.now(dt.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    with CODES_LOCK:
        data = load_codes()
        data["codes"].append(entry)
        save_codes(data)
    return entry


def check_rate(ip):
    now = time.time()
    with RATE_LOCK:
        recent = [stamp for stamp in RATE_BY_IP.get(ip, []) if now - stamp < RATE_WINDOW_SECONDS]
        recent.append(now)
        RATE_BY_IP[ip] = recent
        if len(recent) > IP_LIMIT:
            raise APIError(429, "too many enrollment attempts from this IP")

        GLOBAL_RATE[:] = [stamp for stamp in GLOBAL_RATE if now - stamp < RATE_WINDOW_SECONDS]
        GLOBAL_RATE.append(now)
        if len(GLOBAL_RATE) > GLOBAL_LIMIT:
            raise APIError(429, "too many enrollment attempts")


def ip_allowed(ip):
    if not ALLOWED_NETWORKS:
        return True
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in network for network in ALLOWED_NETWORKS)


class LeafWikiClient:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def request(self, method, path, body=None, headers=None):
        data = None
        request_headers = {"Accept": "application/json"}
        if headers:
            request_headers.update(headers)
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            BASE_URL + path, data=data, method=method, headers=request_headers
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read().decode("utf-8", "replace")
                status = response.status
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", "replace")
            raise APIError(error.code, raw) from error
        except urllib.error.URLError as error:
            raise APIError(0, f"could not reach LeafWiki: {error.reason}") from error

        if not raw:
            return status, None
        try:
            return status, json.loads(raw)
        except json.JSONDecodeError:
            return status, raw

    def csrf_token(self):
        for cookie in self.jar:
            if cookie.name in {"__Host-leafwiki_csrf", "leafwiki_csrf"}:
                return cookie.value
        raise APIError(0, "CSRF cookie missing")

    def login_admin(self):
        self.request("GET", "/api/config")
        _, data = self.request(
            "POST",
            "/api/auth/login",
            {"identifier": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )
        if not isinstance(data, dict) or not data.get("user"):
            raise APIError(0, "admin login failed")
        if data["user"].get("role") != "admin":
            raise APIError(0, "configured account is not an admin")

    def user_by_name(self, username):
        _, users = self.request("GET", "/api/users")
        if not isinstance(users, list):
            raise APIError(0, "unexpected user list response")
        for user in users:
            if user.get("username") == username:
                return user
        return None

    def create_user(self, username, email, role):
        _, user = self.request(
            "POST",
            "/api/users",
            {
                "username": username,
                "email": email,
                "password": secrets.token_hex(24),
                "role": role,
            },
            {"X-CSRF-Token": self.csrf_token()},
        )
        if not isinstance(user, dict) or not user.get("id"):
            raise APIError(0, "unexpected create-user response")
        return user

    def create_key(self, name, user_id, role, expires_at):
        _, data = self.request(
            "POST",
            "/api/api-keys",
            {
                "name": name,
                "userId": user_id,
                "role": role,
                "expiresAt": expires_at,
            },
            {"X-CSRF-Token": self.csrf_token()},
        )
        if not isinstance(data, dict) or not data.get("secret"):
            raise APIError(0, "unexpected create-key response")
        return data

    def delete_user(self, user_id):
        self.request(
            "DELETE",
            f"/api/users/{user_id}",
            headers={"X-CSRF-Token": self.csrf_token()},
        )


def get_admin_client():
    """Return one authenticated admin client and re-login when the session expires."""
    global ADMIN_SESSION
    if ADMIN_SESSION is None:
        client = LeafWikiClient()
        client.login_admin()
        ADMIN_SESSION = client
        return ADMIN_SESSION

    try:
        ADMIN_SESSION.request("GET", "/api/users")
    except APIError as error:
        if error.status != 401:
            raise
        client = LeafWikiClient()
        client.login_admin()
        ADMIN_SESSION = client
    return ADMIN_SESSION


def register_agent(body, client_ip):
    code = str(body.get("code", "")).strip()
    agent_name = str(body.get("agentName", "")).strip()
    email = str(body.get("email", "")).strip()
    key_name = str(body.get("keyName", "")).strip()

    if not code:
        raise APIError(400, "code is required")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,63}", agent_name):
        raise APIError(400, "agentName must be 3-64 chars: letters, digits, . _ -")
    if not email:
        email = f"{agent_name}@agents.local"
    if not key_name:
        key_name = f"{agent_name}-{dt.datetime.now().strftime('%Y%m%d')}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,80}", key_name):
        raise APIError(400, "keyName contains invalid characters")

    check_rate(client_ip)
    claim = claim_code(code, agent_name)
    role = claim.get("role")
    if role not in {"viewer", "editor"}:
        release_code(code, agent_name)
        raise APIError(500, "enrollment code has invalid role")

    expires_days = int(claim.get("keyExpiresDays", 90))
    expires_at = None
    if expires_days > 0:
        expires_at = (
            dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=expires_days)
        ).isoformat().replace("+00:00", "Z")

    created_user = None
    try:
        with ADMIN_LOCK:
            client = get_admin_client()
            existing = client.user_by_name(agent_name)
            if existing:
                release_code(code, agent_name)
                raise APIError(409, "agent user already exists; ask admin to rotate its key")

            created_user = client.create_user(agent_name, email, role)
            created_key = client.create_key(key_name, created_user["id"], role, expires_at)

            record = {
                "baseUrl": BASE_URL,
                "agentUsername": agent_name,
                "agentEmail": email,
                "role": role,
                "memoryRoot": f"agents/{agent_name}/",
                "memoryPolicyVersion": "1.0",
                "keyName": key_name,
                "expiresAt": expires_at,
                "apiKeyId": created_key["key"]["id"],
                "apiKey": created_key["secret"],
            }
            REGISTRY_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                REGISTRY_DIR.chmod(0o700)
            except OSError:
                pass
            target = REGISTRY_DIR / f"{safe_name(agent_name)}.json"
            target.write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            try:
                target.chmod(0o600)
            except OSError:
                pass
            return record
    except Exception:
        if created_user:
            try:
                with ADMIN_LOCK:
                    get_admin_client().delete_user(created_user["id"])
            except Exception:
                pass
        release_code(code, agent_name)
        raise


class Handler(BaseHTTPRequestHandler):
    server_version = "LeafWikiAgentEnroll/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write(
            json.dumps({"time": iso_now(), "message": fmt % args}) + "\n"
        )

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def client_ip(self):
        if TRUST_PROXY:
            forwarded = self.headers.get("X-Real-IP")
            if not forwarded:
                forwarded = self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            if forwarded:
                return forwarded
        return self.client_address[0]

    def do_GET(self):
        path = urlparse(self.path).path
        if path in {"/health", "/agent-enroll/health"}:
            self.send_json(200, {"status": "ok"})
            return
        if path in {"/issue", "/agent-enroll/issue"}:
            client_ip = self.client_ip()
            if not ip_allowed(client_ip):
                self.send_json(403, {"error": "IP not allowed"})
                return
            try:
                check_rate(client_ip)
                query = parse_qs(urlparse(self.path).query)
                role = (query.get("role") or ["viewer"])[0]
                if role not in {"viewer", "editor"}:
                    self.send_json(400, {"error": "role must be viewer or editor"})
                    return
                if role == "editor":
                    if not EDITOR_TOKEN:
                        self.send_json(403, {"error": "editor self-service is disabled"})
                        return
                    supplied = self.headers.get("X-Editor-Token", "")
                    if not hmac.compare_digest(supplied, EDITOR_TOKEN):
                        self.send_json(403, {"error": "invalid editor token"})
                        return
                entry = issue_self_service_code(role)
                self.send_json(200, {
                    "code": entry["code"],
                    "role": entry["role"],
                    "expiresAt": entry["expiresAt"],
                })
            except APIError as error:
                self.send_json(error.status or 500, {"error": str(error)})
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in {"/register", "/agent-enroll/register"}:
            self.send_json(404, {"error": "not found"})
            return

        client_ip = self.client_ip()
        if not ip_allowed(client_ip):
            self.send_json(403, {"error": "IP not allowed"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY:
                raise APIError(413, "request body missing or too large")
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise APIError(400, "request body must be an object")
            record = register_agent(body, client_ip)
            self.send_json(201, record)
        except APIError as error:
            self.send_json(error.status or 500, {"error": str(error)})
        except json.JSONDecodeError:
            self.send_json(400, {"error": "invalid JSON"})
        except Exception as error:
            sys.stderr.write(
                json.dumps({"time": iso_now(), "error": repr(error)}) + "\n"
            )
            self.send_json(500, {"error": "internal error"})


def main():
    if not ADMIN_USERNAME or not ADMIN_PASSWORD:
        raise SystemExit("set LEAFWIKI_ADMIN_USERNAME and LEAFWIKI_ADMIN_PASSWORD")
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    print(
        json.dumps(
            {
                "status": "listening",
                "bind": BIND,
                "port": PORT,
                "baseUrl": BASE_URL,
                "codesFile": str(CODES_FILE),
                "registryDir": str(REGISTRY_DIR),
            }
        ),
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
