#!/usr/bin/env python3
"""harness 知识库工具（Nowen Note 版，多 agent 隔离）

环境变量（均可省略，有默认）:
  KB_BASE      知识库 API 地址，默认 https://wiki.kkuu.de
               本机可设 http://127.0.0.1:30011
  KB_ADMIN     登录账号，默认 admin
  KB_PASSWORD  登录密码（环境变量，或 ~/.kb_password 文件，无硬编码默认值）
  KB_AGENT     本 agent 名，默认 harness（决定写入哪个顶层目录，隔离关键）
               也可写 ~/.kb_agent 文件（内容即 agent 名）

用法:
  kb context                                   # 开工：注入人格+规范+本 agent 近期记忆
  kb search "关键词"                            # 全局全文搜索
  kb remember --ws <工作区> --text "..." --type decision  # 写当日记忆（只写本 agent 目录）
  kb wrap-up                                   # 收尾检查
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("KB_BASE", "https://wiki.kkuu.de").rstrip("/")
ADMIN = os.environ.get("KB_ADMIN", "admin")
AGENT_FILE = os.path.expanduser("~/.kb_agent")
TOKEN_FILE = os.path.expanduser("~/.kb_token")


def _password():
    p = os.environ.get("KB_PASSWORD")
    if p:
        return p
    try:
        return open(os.path.expanduser("~/.kb_password")).read().strip()
    except Exception:
        return ""


PASSWORD = _password()

SHARED = "00-全局"
DAILY = "01-每日记录"
CONVENTION = "00-身份与约定"


def agent_name():
    if os.environ.get("KB_AGENT"):
        return os.environ["KB_AGENT"].strip()
    try:
        v = open(AGENT_FILE).read().strip()
        if v:
            return v
    except Exception:
        pass
    return "harness"


def _request(method, path, body=None, token=None, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def login():
    try:
        cached = open(TOKEN_FILE).read().strip()
        if cached:
            s, _ = _request("GET", "/api/notebooks", token=cached)
            if s == 200:
                return cached
    except Exception:
        pass
    s, resp = _request("POST", "/api/auth/login", {"username": ADMIN, "password": PASSWORD})
    if s != 200 or not isinstance(resp, dict) or not resp.get("token"):
        sys.exit(f"登录失败 HTTP {s}: {resp}")
    tok = resp["token"]
    with open(TOKEN_FILE, "w") as f:
        f.write(tok)
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass
    return tok


def get_notebooks():
    s, nbs = _request("GET", "/api/notebooks", token=login())
    if s != 200:
        sys.exit(f"读取笔记本失败 HTTP {s}")
    return [n for n in nbs if not n.get("isDeleted")]


def get_notes():
    s, notes = _request("GET", "/api/notes", token=login())
    if s != 200:
        sys.exit(f"读取笔记失败 HTTP {s}")
    return [n for n in notes if not n.get("isTrashed")]


def create_notebook(name, parent_id):
    s, r = _request("POST", "/api/notebooks", {"name": name, "parentId": parent_id}, token=login())
    return (r or {}).get("id") if s in (200, 201) else None


def find_or_create(name, parent_id):
    nbs = get_notebooks()
    for n in nbs:
        if n["name"] == name and (n.get("parentId") or None) == (parent_id or None):
            return n["id"]
    return create_notebook(name, parent_id)


def note_content(nid):
    s, full = _request("GET", f"/api/notes/{nid}", token=login())
    return (full or {}).get("content") or ""


def _today():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d")


def cmd_context(args):
    me = agent_name()
    nbs = get_notebooks()
    notes = get_notes()
    print(f"【会话上下文】agent={me}  知识库={BASE}")
    print("=" * 64)
    shared_id = next((n["id"] for n in nbs if n["name"] == SHARED and not n.get("parentId")), None)
    agent_id = next((n["id"] for n in nbs if n["name"] == me and not n.get("parentId")), None)
    conv_id = next((n["id"] for n in nbs if n["name"] == CONVENTION and n.get("parentId") == agent_id), None) if agent_id else None
    for t in ("SOUL", "AGENTS_角色设定", "AGENTS 通用守则"):
        n = next((x for x in notes if x.get("title") == t and x.get("notebookId") == shared_id), None)
        if n:
            print(f"\n### {t}\n{note_content(n['id'])[:500]}")
    for t in ("约定", "索引"):
        n = next((x for x in notes if x.get("title") == t and x.get("notebookId") == conv_id), None)
        if n:
            print(f"\n### {t}\n{note_content(n['id'])[:700]}")
    agent_nb_ids = {n["id"] for n in nbs if n.get("parentId") == agent_id or n["id"] == agent_id}
    daily = [x for x in notes if x["title"][:4].isdigit() and x.get("notebookId") in agent_nb_ids]
    daily.sort(key=lambda x: x["title"], reverse=True)
    print("\n### 近期每日记录（本 agent）")
    for x in daily[:6]:
        print(f"  - {x['title']}")
    print("=" * 64)


def cmd_search(args):
    s, res = _request("GET", "/api/search", token=login(), params={"q": args.q})
    if s != 200:
        sys.exit(f"搜索失败 HTTP {s}: {res}")
    items = res if isinstance(res, list) else (res or {}).get("results") or (res or {}).get("items") or []
    print(f"搜索「{args.q}」命中 {len(items)} 条：")
    for it in items[:12]:
        title = it.get("title") or it.get("name") or "?"
        snippet = (it.get("snippet") or it.get("contentText") or it.get("content") or "")[:120]
        print(f"  - {title}: {snippet}")


def cmd_remember(args):
    me = agent_name()
    agent_id = find_or_create(me, None)
    daily_id = find_or_create(DAILY, agent_id)
    ws_id = find_or_create(args.ws, daily_id)
    t = _today()
    notes = get_notes()
    note = next((x for x in notes if x.get("title") == t and x.get("notebookId") == ws_id), None)
    entry = f"## {args.type} | {args.text}\n"
    if note:
        content = note_content(note["id"])
        new = content.rstrip() + "\n\n" + entry + "\n"
        s2, _ = _request("PUT", f"/api/notes/{note['id']}", token=login(),
                         body={"title": t, "content": new, "version": note.get("version")})
        print(f"追加 {me}/{DAILY}/{args.ws}/{t} -> {s2}")
    else:
        s2, _ = _request("POST", "/api/notes", token=login(),
                         body={"notebookId": ws_id, "title": t,
                               "content": f"# {t}\n\n" + entry, "contentFormat": "markdown"})
        print(f"新建 {me}/{DAILY}/{args.ws}/{t} -> {s2}")


def cmd_wrapup(args):
    me = agent_name()
    print(f"=== 收尾检查（agent={me}）===")
    print("1. 本会话记忆是否写入 01-每日记录/<工作区>/YYYY-MM-DD ？")
    print("2. 稳定事实是否沉淀到 02-主题知识 并加了 [[链接]] ？")
    print("3. 未确定内容是否进 05-收件箱 ？")
    print("4. 00-身份与约定/索引 是否更新 ？")
    print("5. 是否有「漏掉的胜利/决策」没记 ？")


def main():
    p = argparse.ArgumentParser(prog="kb", description="harness 知识库工具（多 agent 隔离）")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("context", help="开工注入上下文")
    s1 = sub.add_parser("search", help="全局全文搜索"); s1.add_argument("q")
    s2 = sub.add_parser("remember", help="写当日记忆（只写本 agent 目录）")
    s2.add_argument("--ws", required=True, help="工作区名，如 seek/symedia/dsh/LeafWiki，可任意")
    s2.add_argument("--text", required=True)
    s2.add_argument("--type", default="note", help="note|decision|fact|todo")
    sub.add_parser("wrap-up", help="收尾检查")
    args = p.parse_args()
    {"context": cmd_context, "search": cmd_search, "remember": cmd_remember, "wrap-up": cmd_wrapup}.get(args.cmd, lambda a: p.print_help())(args)


if __name__ == "__main__":
    main()
