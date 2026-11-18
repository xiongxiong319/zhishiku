#!/usr/bin/env python3
"""LeafWiki Agent 一键接入脚本。

用法（Agent 侧，只需一个一次性注册码 + 唯一 agent 名）：
    python3 onboard_agent.py --code 'lw_enroll_xxx' --agent-name 'my-agent-01'

可选：
    --email 'agent@example.com'
    --base-url 'https://wiki.kkuu.de'
    --enroll-url 'https://wiki.kkuu.de/agent-enroll/register'
    --output-dir '$HOME/.leafwiki/agent-keys'

自动完成：
    1. 注册（一次性注册码 -> 独立用户 + 独立 API Key + memoryRoot）
    2. 写凭据文件到 <output-dir>/<agent-name>.json（600）
    3. auth-check 校验 Key
    4. editor 角色执行 memory-init：在 memoryRoot 下建目录骨架
    5. 打印摘要（不打印 API Key）

依赖：仅 Python 3 标准库，无需 pip 安装任何东西。
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_ENROLL_URL = "https://wiki.kkuu.de/agent-enroll/register"
DEFAULT_BASE_URL = "https://wiki.kkuu.de"
POLICY_VERSION = "1.0"
USER_AGENT = "leafwiki-agent-onboard/1.0"


def die(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def http_json(url, method="GET", body=None, bearer=None, timeout=30):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def register(enroll_url, code, agent_name, email):
    payload = {"code": code, "agentName": agent_name}
    if email:
        payload["email"] = email
    status, data = http_json(enroll_url, "POST", payload)
    if status != 201 or not isinstance(data, dict) or not data.get("apiKey"):
        print(f"注册失败 HTTP {status}: {data}", file=sys.stderr)
        if status == 401:
            die("注册码无效或已被使用。请向管理员索取一个新的注册码。")
        if status == 409:
            die("该 agent 名已存在。请管理员为同一 agent 用户轮换 Key，不要重新注册。")
        if status == 410:
            die("注册码已过期。请向管理员索取一个新的注册码。")
        die("注册失败，请检查注册服务地址和网络。")
    return data


def save_credentials(record, output_dir):
    agent_name = record["agentUsername"]
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", agent_name)
    out = Path(output_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        out.chmod(0o700)
    except OSError:
        pass
    target = out / f"{safe}.json"
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target


def api(base_url, key, method, path, body=None, params=None):
    url = base_url.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    status, data = http_json(url, method, body, bearer=key)
    return status, data


def auth_check(base_url, key, expires_at):
    status, _ = api(base_url, key, "GET", "/api/tree")
    if status == 200:
        return "valid"
    # 本地过期判断
    if expires_at:
        try:
            exp = dt.datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if exp <= dt.datetime.now(dt.timezone.utc):
                return "expired"
        except ValueError:
            pass
    return "revoked_or_invalid"


def get_page(base_url, key, path):
    status, page = api(base_url, key, "GET", "/api/pages/by-path", params={"path": path})
    if status == 200 and isinstance(page, dict):
        return page
    return None


def ensure_node(base_url, key, title, slug, kind, parent, content=None):
    """幂等地创建 section/page，返回该节点的 dict（含 id/path）。

    parent 为父节点 dict（含 path/id）或 None（根）。content 只在首次创建时写入，
    已存在节点不覆盖，避免清空 Agent 已有内容。
    """
    parent_path = (parent.get("path") or "") if parent else ""
    full_path = f"{parent_path}/{slug}" if parent_path else slug

    page = get_page(base_url, key, full_path)
    created_new = False
    if page is not None and page.get("kind") == kind:
        node = page
    else:
        body = {"title": title, "slug": slug,
                "parentId": (parent.get("id") if parent else None), "kind": kind}
        status, created = api(base_url, key, "POST", "/api/pages", body=body)
        if status in (200, 201) and isinstance(created, dict) and created.get("id"):
            node = created
            created_new = True
        else:
            # 可能已存在（并发/重复创建）
            page = get_page(base_url, key, full_path)
            if page is not None:
                return page
            die(f"创建节点失败：{full_path} (HTTP {status} {created})")

    if content is not None and created_new and node.get("id"):
        full = get_page(base_url, key, full_path)
        if full and full.get("id"):
            status, updated = api(
                base_url, key, "PUT", f"/api/pages/{full['id']}",
                body={
                    "version": full.get("version"),
                    "title": full.get("title"),
                    "slug": full.get("slug"),
                    "content": content,
                    "tags": full.get("tags", []),
                    "properties": full.get("properties") or {},
                },
            )
            if status in (200, 201) and isinstance(updated, dict):
                node = updated
    return node


def memory_init(base_url, key, record):
    name = record["agentUsername"]
    role = record["role"]
    memory_root = record.get("memoryRoot") or f"agents/{name}/"
    root_name = memory_root.strip("/").split("/")[0]  # "agents"

    # 1) 顶层 agents section
    agents = ensure_node(base_url, key, root_name, root_name, "section", None)

    # 2) agents/<name> section
    mine = ensure_node(base_url, key, name, name, "section", agents)

    # 3) 子结构（LeafWiki slug 只允许字母/数字/连字符，故 _meta 用 slug=meta）
    meta = ensure_node(base_url, key, "meta", "meta", "section", mine)
    ensure_node(base_url, key, "daily", "daily", "section", mine)
    ensure_node(base_url, key, "weekly", "weekly", "section", mine)
    ensure_node(base_url, key, "monthly", "monthly", "section", mine)
    ensure_node(base_url, key, "topics", "topics", "section", mine)
    inbox = ensure_node(base_url, key, "inbox", "inbox", "section", mine)

    # 4) 关键页面
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    ensure_node(base_url, key, "profile", "profile", "page", meta,
                content=f"agent: {name}\nrole: {role}\ncreated: {now}\n")
    ensure_node(base_url, key, "policy", "policy", "page", meta,
                content=(
                    f"contract_version: {POLICY_VERSION}\n"
                    f"memory_root: agents/{name}/\n"
                    f"allowed_write_prefix: agents/{name}/\n"
                    "read_before_write: true\n"
                    "daily_path: daily/YYYY-MM-DD\n"
                    f"required_tags: agent:{name}, memory:daily, type:<type>\n"
                    "forbidden: secrets, credentials, raw chat logs\n"
                    "auth_check: python3 leafwiki_agent.py auth-check\n"
                    "on_401: stop and report AUTH_KEY_EXPIRED or AUTH_KEY_REVOKED_OR_INVALID\n"
                    "forbidden_on_401: re-register, new user, guess key, switch memoryRoot\n"
                ))
    ensure_node(base_url, key, "index", "index-page", "page", meta, content="")
    ensure_node(base_url, key, "unverified-notes", "unverified-notes", "page", inbox, content="")

    return memory_root


def main():
    parser = argparse.ArgumentParser(description="LeafWiki Agent 一键接入")
    parser.add_argument("--code", required=True, help="一次性注册码")
    parser.add_argument("--agent-name", required=True, help="唯一 agent 名（3-64 位 字母/数字/._-）")
    parser.add_argument("--email", default=None)
    parser.add_argument("--enroll-url", default=os.environ.get("LEAFWIKI_ENROLL_URL", DEFAULT_ENROLL_URL))
    parser.add_argument("--base-url", default=os.environ.get("LEAFWIKI_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--output-dir", default=str(Path.home() / ".leafwiki" / "agent-keys"))
    parser.add_argument("--skip-memory-init", action="store_true", help="跳过 memory-init（viewer 默认跳过）")
    args = parser.parse_args()

    code = args.code.strip()
    name = args.agent_name.strip()
    if not code:
        die("--code 不能为空")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,63}", name):
        die("--agent-name 必须 3-64 位，仅字母/数字/._-，且以字母或数字开头")

    enroll_url = args.enroll_url.rstrip("/")
    if not enroll_url.endswith("/register"):
        enroll_url += "/register"

    print(f"[1/4] 注册 agent：{name}")
    record = register(enroll_url, code, name, args.email)

    role = record.get("role", "?")
    memory_root = record.get("memoryRoot") or f"agents/{name}/"
    expires = record.get("expiresAt")
    key_id = record.get("apiKeyId")

    print(f"[2/4] 写凭据文件…")
    cred_path = save_credentials(record, args.output_dir)

    print(f"[3/4] auth-check…")
    state = auth_check(args.base_url, record["apiKey"], expires)
    if state != "valid":
        print(f"      认证状态：{state}", file=sys.stderr)
        die("认证失败。请向管理员为同一 agent 用户轮换 Key，不要重新注册、不要猜 Key。")
    print(f"      认证状态：{state}")

    if role == "editor" and not args.skip_memory_init:
        print(f"[4/4] memory-init（{memory_root}）…")
        memory_init(args.base_url, record["apiKey"], record)
    else:
        print(f"[4/4] memory-init 跳过（role={role}）")

    print("\n===== 接入完成 =====")
    print(f"agent       : {name}")
    print(f"role        : {role}")
    print(f"memoryRoot  : {memory_root}")
    print(f"policy      : {record.get('memoryPolicyVersion')}")
    print(f"apiKeyId    : {key_id}")
    print(f"expiresAt   : {expires}")
    print(f"凭据文件    : {cred_path}")
    print("\n后续使用：")
    print(f"  export LEAFWIKI_BASE_URL={args.base_url}")
    print(f"  export LEAFWIKI_API_KEY_FILE={cred_path}")
    print("  python3 leafwiki_agent.py auth-check")
    print("  python3 leafwiki_agent.py tree / search / read / create / append")
    print("\nAPI Key 已写入凭据文件，未打印。")


if __name__ == "__main__":
    main()
