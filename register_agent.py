#!/usr/bin/env python3
"""Register the current agent and save its LeafWiki credentials."""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


def normalize_url(value):
    value = value.rstrip("/")
    if value.endswith("/register"):
        return value
    return value + "/register"


def main():
    parser = argparse.ArgumentParser(
        description="Register an agent with a one-time LeafWiki enrollment code"
    )
    parser.add_argument(
        "--server",
        default=os.environ.get(
            "LEAFWIKI_ENROLL_URL", "https://wiki.kkuu.de/agent-enroll/register"
        ),
    )
    parser.add_argument(
        "--code", default=os.environ.get("LEAFWIKI_ENROLL_CODE", "")
    )
    parser.add_argument("--agent-name", required=True)
    parser.add_argument("--email")
    parser.add_argument("--key-name")
    parser.add_argument(
        "--output-dir",
        default=str(Path.home() / ".leafwiki" / "agent-keys"),
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    code = args.code.strip()
    if not code:
        raise SystemExit("set --code or LEAFWIKI_ENROLL_CODE")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        output_dir.chmod(0o700)
    except OSError:
        pass
    safe_agent = re.sub(r"[^A-Za-z0-9_.-]+", "_", args.agent_name)
    target = output_dir / f"{safe_agent}.json"
    if target.exists() and not args.force:
        raise SystemExit(f"credential file already exists: {target}; use --force to overwrite")

    payload = {
        "code": code,
        "agentName": args.agent_name,
    }
    if args.email:
        payload["email"] = args.email
    if args.key_name:
        payload["keyName"] = args.key_name

    request = urllib.request.Request(
        normalize_url(args.server),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "leafwiki-agent-register/1.0",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        print(f"registration failed: HTTP {error.code}: {raw}", file=sys.stderr)
        if error.code == 409:
            print("already registered; ask the admin to rotate the key.", file=sys.stderr)
        elif error.code == 410:
            print("enrollment code expired; request a new code.", file=sys.stderr)
        elif error.code == 401:
            print("enrollment code is invalid or already used.", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as error:
        raise SystemExit(f"could not reach enrollment service: {error.reason}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise SystemExit("enrollment service returned non-JSON data")
    if status != 201 or not data.get("apiKey"):
        raise SystemExit(f"unexpected enrollment response: {data!r}")

    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass

    print(f"saved credentials to {target}")
    print(
        f"agent={data.get('agentUsername')} role={data.get('role')} "
        f"memoryRoot={data.get('memoryRoot')} "
        f"policy={data.get('memoryPolicyVersion')} "
        f"expires={data.get('expiresAt')}"
    )
    print("The API key is stored in the file and is not printed.")


if __name__ == "__main__":
    main()
