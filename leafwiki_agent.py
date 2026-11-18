#!/usr/bin/env python3
"""Small LeafWiki API client intended for agent shells and automation."""

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_BASE_URL = "https://wiki.kkuu.de"
TIMEOUT_SECONDS = 30
SUPPORTED_POLICY_VERSION = "1.0"
EXPIRING_SOON_DAYS = 7


def die(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def credential():
    key = os.environ.get("LEAFWIKI_API_KEY", "").strip()
    if key:
        return {"apiKey": key, "source": "LEAFWIKI_API_KEY"}

    key_file = os.environ.get("LEAFWIKI_API_KEY_FILE", "").strip()
    if key_file:
        path = Path(key_file)
        if not path.is_file():
            die(f"AUTH_KEY_MISSING: LEAFWIKI_API_KEY_FILE does not exist: {key_file}")
        raw = path.read_text(encoding="utf-8").strip()
        if raw.startswith("{"):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                die(f"AUTH_KEY_INVALID_FILE: LEAFWIKI_API_KEY_FILE is not valid JSON: {key_file}")
            for field in ("apiKey", "secret", "key"):
                value = data.get(field)
                if isinstance(value, str) and value.strip():
                    data = dict(data)
                    data["apiKey"] = value.strip()
                    data["source"] = str(path)
                    return data
            die(f"AUTH_KEY_INVALID_FILE: LEAFWIKI_API_KEY_FILE has no apiKey field: {key_file}")
        return {"apiKey": raw, "source": str(path)}

    die("AUTH_KEY_MISSING: set LEAFWIKI_API_KEY or LEAFWIKI_API_KEY_FILE")


def api_key():
    return credential()["apiKey"]


def parse_expiry(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed
    except ValueError:
        return None


def credential_state():
    data = credential()
    expires_at = parse_expiry(data.get("expiresAt"))
    now = dt.datetime.now(dt.timezone.utc)
    if expires_at is None:
        state = "unknown"
        days_left = None
    elif expires_at <= now:
        state = "expired"
        days_left = 0
    else:
        days_left = (expires_at - now).total_seconds() / 86400
        state = "expiring_soon" if days_left <= EXPIRING_SOON_DAYS else "valid"

    return {
        "status": state,
        "agentUsername": data.get("agentUsername"),
        "role": data.get("role"),
        "memoryRoot": data.get("memoryRoot"),
        "memoryPolicyVersion": data.get("memoryPolicyVersion"),
        "apiKeyId": data.get("apiKeyId"),
        "expiresAt": data.get("expiresAt"),
        "daysLeft": None if days_left is None else round(days_left, 1),
        "source": data.get("source"),
    }


def auth_guidance(state):
    if state["status"] in {"revoked_or_invalid"}:
        return "AUTH_KEY_REVOKED_OR_INVALID: the API key was rejected. Do not re-register, do not create a second user, and do not guess a new key. Ask the administrator to rotate the key for this same agent user."
    if state["status"] == "expired":
        return "AUTH_KEY_EXPIRED: the API key has expired. Stop work and ask the administrator to rotate the key for the same agent user. Do not re-register and do not guess a new key."
    if state["status"] == "valid_expiry_unknown":
        return "AUTH_KEY_VALID_EXPIRY_UNKNOWN: the key works but has no expiry metadata. Ask the administrator to confirm its expiry."
    if state["status"] == "expiring_soon":
        return "AUTH_KEY_EXPIRING_SOON: the API key expires within 7 days. Continue only if needed and ask the administrator to rotate it."
    if state["status"] == "unknown":
        return "AUTH_KEY_EXPIRY_UNKNOWN: credentials have no expiresAt. Ask the administrator to provide a credential file with expiry metadata."
    return "AUTH_KEY_VALID"


def preflight_credential():
    state = credential_state()
    if state["status"] == "expired":
        print(json.dumps(state, ensure_ascii=False), file=sys.stderr)
        die(auth_guidance(state))
    if state["status"] == "expiring_soon":
        print(auth_guidance(state), file=sys.stderr)
    return state


def api_request(base_url, method, path, params=None, body=None):
    url = base_url.rstrip("/") + path
    if params:
        pairs = []
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, list):
                for item in value:
                    pairs.append((key, str(item)))
            else:
                pairs.append((key, str(value)))
        if pairs:
            url += "?" + urllib.parse.urlencode(pairs)

    data = None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key()}",
        "User-Agent": "leafwiki-agent/1.0",
    }
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, method=method, headers=headers)

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        print(f"LeafWiki HTTP {error.code}: {raw}", file=sys.stderr)
        if error.code == 401:
            state = credential_state()
            if state["status"] == "expired":
                print(auth_guidance(state), file=sys.stderr)
            else:
                print(
                    json.dumps(state, ensure_ascii=False),
                    file=sys.stderr,
                )
                print(
                    "AUTH_KEY_REVOKED_OR_INVALID: the API key was rejected. "
                    "Do not re-register, do not create a second user, and do not guess a new key. "
                    "Ask the administrator to rotate the key for this same agent user.",
                    file=sys.stderr,
                )
        elif error.code == 403:
            print("This API key does not have permission for this action.", file=sys.stderr)
        elif error.code == 409:
            print("Page version conflict: re-read the page and retry.", file=sys.stderr)
        elif error.code == 429:
            print("Rate limited: wait and retry.", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as error:
        die(f"Could not reach LeafWiki: {error.reason}")

    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def read_text_argument(value, file_value):
    if value is not None and file_value is not None:
        die("use only one of --content/--text and --content-file/--text-file")
    if file_value is not None:
        if file_value == "-":
            return sys.stdin.read()
        return Path(file_value).read_text(encoding="utf-8")
    return value


def get_page(base_url, path):
    page = api_request(base_url, "GET", "/api/pages/by-path", {"path": path})
    if not isinstance(page, dict):
        die(f"Unexpected page response: {page!r}")
    return page


def update_existing_page(base_url, page, title=None, slug=None, content=None, tags=None):
    body = {
        "version": page["version"],
        "title": title if title is not None else page["title"],
        "slug": slug if slug is not None else page["slug"],
        "content": content if content is not None else page.get("content", ""),
        "tags": tags if tags is not None else page.get("tags", []),
        "properties": page.get("properties") or {},
    }
    return api_request(base_url, "PUT", f"/api/pages/{page['id']}", body=body)


def print_tree(node, depth=0):
    kind = node.get("kind", "page")
    title = node.get("title", "")
    path = node.get("path", "")
    print(f"{'  ' * depth}[{kind}] {title} ({path})")
    for child in node.get("children") or []:
        print_tree(child, depth + 1)


def command_tree(args):
    data = api_request(args.base_url, "GET", "/api/tree")
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print_tree(data)


def command_auth_check(args):
    state = credential_state()
    state["guidance"] = auth_guidance(state)
    if state["status"] == "expired":
        state["keyAccepted"] = False
        print(json.dumps(state, ensure_ascii=False, indent=2))
        sys.exit(1)

    try:
        api_request(args.base_url, "GET", "/api/tree")
        state["keyAccepted"] = True
        if state["status"] == "unknown":
            state["status"] = "valid_expiry_unknown"
    except SystemExit:
        state["keyAccepted"] = False
        if state["status"] != "expiring_soon":
            state["status"] = "revoked_or_invalid"
        state["guidance"] = auth_guidance(state)

    print(json.dumps(state, ensure_ascii=False, indent=2))
    if not state["keyAccepted"]:
        sys.exit(1)


def command_search(args):
    if not args.query and not args.tag:
        die("provide a query or at least one --tag")
    data = api_request(
        args.base_url,
        "GET",
        "/api/search",
        {
            "q": args.query or None,
            "limit": args.limit,
            "offset": args.offset,
            "tags": args.tag or None,
        },
    )
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(f"count={data.get('count', 0)}")
    for item in data.get("items") or []:
        print(f"\n{item.get('path')}\n  {item.get('title')}\n  {item.get('excerpt', '')}")


def command_read(args):
    page = get_page(args.base_url, args.path)
    if args.json:
        print(json.dumps(page, ensure_ascii=False, indent=2))
    else:
        print(page.get("content", ""))


def command_create(args):
    content = read_text_argument(args.content, args.content_file)
    parent_id = args.parent_id or None
    if parent_id == "root":
        parent_id = None

    created = api_request(
        args.base_url,
        "POST",
        "/api/pages",
        body={
            "title": args.title,
            "slug": args.slug,
            "parentId": parent_id,
            "kind": args.kind,
        },
    )

    if content is not None:
        path = created.get("path")
        if not path:
            die(f"Page was created but no path was returned: {created!r}")
        page = get_page(args.base_url, path)
        created = update_existing_page(args.base_url, page, content=content)

    if args.json:
        print(json.dumps(created, ensure_ascii=False, indent=2))
    else:
        print(f"created: {created.get('path')}")


def command_update(args):
    content = read_text_argument(args.content, args.content_file)
    page = get_page(args.base_url, args.path)
    updated = update_existing_page(
        args.base_url,
        page,
        title=args.title,
        slug=args.slug,
        content=content,
        tags=args.tag,
    )
    if args.json:
        print(json.dumps(updated, ensure_ascii=False, indent=2))
    else:
        print(f"updated: {updated.get('path')} version={updated.get('version')}")


def command_append(args):
    text = read_text_argument(args.text, args.text_file)
    if not text:
        die("append text is empty")
    page = get_page(args.base_url, args.path)
    existing = page.get("content", "").rstrip()
    content = f"{existing}\n\n{text}\n" if existing else f"{text}\n"
    updated = update_existing_page(args.base_url, page, content=content)
    if args.json:
        print(json.dumps(updated, ensure_ascii=False, indent=2))
    else:
        print(f"appended: {updated.get('path')} version={updated.get('version')}")


def build_parser():
    parser = argparse.ArgumentParser(description="Minimal LeafWiki client for agents")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("LEAFWIKI_BASE_URL", DEFAULT_BASE_URL),
        help="LeafWiki base URL, default from LEAFWIKI_BASE_URL",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    tree = subparsers.add_parser("tree", help="print the page tree")
    tree.add_argument("--json", action="store_true")
    tree.set_defaults(func=command_tree)

    auth_check = subparsers.add_parser(
        "auth-check",
        help="validate the API key and print credential expiry metadata",
    )
    auth_check.add_argument("--json", action="store_true")
    auth_check.set_defaults(func=command_auth_check)

    search = subparsers.add_parser("search", help="full-text search")
    search.add_argument("query", nargs="?")
    search.add_argument("--tag", action="append", default=[])
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--offset", type=int, default=0)
    search.add_argument("--json", action="store_true")
    search.set_defaults(func=command_search)

    read = subparsers.add_parser("read", help="read a page by path")
    read.add_argument("path")
    read.add_argument("--json", action="store_true")
    read.set_defaults(func=command_read)

    create = subparsers.add_parser("create", help="create a page or section")
    create.add_argument("--title", required=True)
    create.add_argument("--slug", required=True)
    create.add_argument("--parent-id", default="")
    create.add_argument("--kind", choices=["page", "section"], default="page")
    create.add_argument("--content")
    create.add_argument("--content-file")
    create.add_argument("--json", action="store_true")
    create.set_defaults(func=command_create)

    update = subparsers.add_parser("update", help="replace a page body")
    update.add_argument("path")
    update.add_argument("--title")
    update.add_argument("--slug")
    update.add_argument("--content")
    update.add_argument("--content-file")
    update.add_argument("--tag", action="append")
    update.add_argument("--json", action="store_true")
    update.set_defaults(func=command_update)

    append = subparsers.add_parser("append", help="append text to a page")
    append.add_argument("path")
    append.add_argument("--text")
    append.add_argument("--text-file")
    append.add_argument("--json", action="store_true")
    append.set_defaults(func=command_append)
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.command != "auth-check":
        preflight_credential()
    args.func(args)


if __name__ == "__main__":
    main()
