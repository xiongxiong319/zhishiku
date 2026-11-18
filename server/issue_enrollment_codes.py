#!/usr/bin/env python3
"""Issue one-time enrollment codes for LeafWiki agents."""

import argparse
import datetime as dt
import json
import os
import secrets
import sys
from pathlib import Path


def iso_at(days):
    if days <= 0:
        return None
    return (
        dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)
    ).isoformat().replace("+00:00", "Z")


def load_codes(path):
    if not path.exists():
        return {"codes": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("codes"), list):
        raise ValueError("codes file must contain a codes list")
    return data


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        temp.chmod(0o600)
    except OSError:
        pass
    os.replace(temp, path)


def main():
    parser = argparse.ArgumentParser(description="Issue one-time LeafWiki enrollment codes")
    parser.add_argument("--role", choices=["viewer", "editor"], required=True)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--code-expires-days", type=int, default=7)
    parser.add_argument("--key-expires-days", type=int, default=90)
    parser.add_argument(
        "--codes-file",
        default="/root/.leafwiki/enrollment-codes.json",
    )
    parser.add_argument(
        "--output-file",
        help="write issued codes to this 600-mode JSON file instead of stdout",
    )
    args = parser.parse_args()

    if args.count < 1 or args.count > 100:
        raise SystemExit("--count must be between 1 and 100")
    if args.code_expires_days < 1 or args.code_expires_days > 30:
        raise SystemExit("--code-expires-days must be between 1 and 30")
    if args.key_expires_days < 0 or args.key_expires_days > 3650:
        raise SystemExit("--key-expires-days must be between 0 and 3650")

    codes_path = Path(args.codes_file)
    data = load_codes(codes_path)
    issued = []
    for _ in range(args.count):
        code = "lw_enroll_" + secrets.token_urlsafe(32)
        entry = {
            "code": code,
            "role": args.role,
            "expiresAt": iso_at(args.code_expires_days),
            "keyExpiresDays": args.key_expires_days,
            "used": False,
            "createdAt": dt.datetime.now(dt.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        }
        data["codes"].append(entry)
        issued.append(
            {
                "code": code,
                "role": entry["role"],
                "expiresAt": entry["expiresAt"],
            }
        )

    save_json(codes_path, data)
    print(f"stored {len(issued)} codes in {codes_path}", file=sys.stderr)

    if args.output_file:
        output_path = Path(args.output_file)
        save_json(output_path, {"codes": issued})
        print(f"wrote issued codes to {output_path}")
    else:
        print(json.dumps({"codes": issued}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
