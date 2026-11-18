# LeafWiki Agent 自助接入教程

> 站点：<https://wiki.kkuu.de>  
> 适用版本：LeafWiki v0.14.2  
> 当前前提：`enableApiKeyManagement = true`，API Key 管理已开启  
> 适用对象：服务端 Agent，以及未来陆续加入的 Codex / Claude / 自动化 Agent  
> 核心方案：**一次性注册码 + Agent 自助注册**，服务端不需要提前维护 Agent 清单

## 1. 结论：不要再维护“未来 Agent 清单”

服务端 Agent 不可能提前知道以后会出现哪些 Agent、叫什么名字、用只读还是可写权限。  
因此不要再用“先写 `agents.json`，再批量创建账号”的方式作为主流程。

改成下面这个模型：

```text
管理员 / 服务端 Agent
  1. 只发放一次性注册码
  2. 注册码分两种角色：viewer / editor
  3. 不预先创建 Agent 用户

某个 Agent 第一次接入
  4. 拿到一个注册码
  5. 自己提交 Agent 名称
  6. 注册服务自动创建 LeafWiki 用户
  7. 注册服务自动创建该 Agent 的独立 API Key
  8. Agent 把凭据保存到本机，开始使用知识库

服务端
  9. 通过用户列表、Key 列表和注册目录知道谁已经接入
```

这样：

- 管理员不需要提前知道 Agent 名字和数量。
- 每个 Agent 仍然有独立用户、独立 Key，能单独撤销。
- 注册码只能用一次，默认 7 天过期。
- API Key 默认 90 天过期。
- 不需要给任何业务 Agent 管理员密码或管理员 Key。
- 以后新增 Agent，只需要再发一个注册码。

## 2. 组成部分

配套工具都在 `leafwiki-agent-kit.zip` 中：

| 文件 | 用途 |
|---|---|
| `agent_enrollment_server.py` | 服务端自助注册服务 |
| `issue_enrollment_codes.py` | 管理员发放一次性注册码 |
| `register_agent.py` | Agent 端自助注册脚本 |
| `leafwiki_agent.py` | Agent 读写知识库的命令行客户端 |
| `create_agent_key.py` | 管理员手动创建或轮换某个 Agent 的 Key |
| `enroll.env.example` | 注册服务环境变量模板 |
| `leafwiki-enroll.service.example` | systemd 服务模板 |
| `nginx-agent-enroll.conf.example` | Nginx 反向代理模板 |

所有脚本只使用 Python 标准库，不需要安装第三方包。

## 3. 服务端部署

以下命令默认在 LeafWiki 所在服务器执行。  
**不要修改现有 `docker-compose.yml`，不要重建 LeafWiki 容器，不要删除 `/opt/leafwiki/data`。**

### 3.1 放入工具

```bash
install -d -m 0755 /opt/leafwiki-agent
unzip -o leafwiki-agent-kit.zip -d /opt/leafwiki-agent
chmod 0755 /opt/leafwiki-agent/*.py
chown -R root:root /opt/leafwiki-agent
```

检查语法：

```bash
python3 -m py_compile \
  /opt/leafwiki-agent/agent_enrollment_server.py \
  /opt/leafwiki-agent/issue_enrollment_codes.py \
  /opt/leafwiki-agent/register_agent.py \
  /opt/leafwiki-agent/leafwiki_agent.py \
  /opt/leafwiki-agent/create_agent_key.py
```

### 3.2 创建专用服务用户和状态目录

不要用主管理员账号长期运行注册服务。  
先创建一个专用系统用户：

```bash
useradd --system \
  --home-dir /var/lib/leafwiki-enroll \
  --create-home \
  --shell /usr/sbin/nologin \
  leafwiki-enroll
```

如果用户已存在，命令报错可以忽略，然后继续：

```bash
install -d -m 0700 -o leafwiki-enroll -g leafwiki-enroll \
  /var/lib/leafwiki-enroll
install -d -m 0700 -o leafwiki-enroll -g leafwiki-enroll \
  /var/lib/leafwiki-enroll/agent-keys
```

### 3.3 创建专用管理员账号

在 LeafWiki 网页中用当前管理员创建一个专用账号：

```text
用户名：agent-provisioner
角色：admin
密码：随机 32 位以上
TOTP：关闭
```

说明：

- 这个账号只给注册服务使用，不交给任何业务 Agent。
- 必须关闭 TOTP，否则无人值守的注册服务无法完成登录。
- 不要把主管理员的个人密码写进服务配置。
- 如果只能暂时使用主管理员账号，配置完成后应尽快换成专用账号并修改主管理员密码。

### 3.4 写环境文件

生成随机密码时不要打印到公共日志：

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

把结果填进 `/etc/leafwiki-agent/enroll.env`：

```bash
install -d -m 0755 /etc/leafwiki-agent
install -m 0600 /opt/leafwiki-agent/enroll.env.example \
  /etc/leafwiki-agent/enroll.env
nano /etc/leafwiki-agent/enroll.env
```

最小配置：

```dotenv
LEAFWIKI_BASE_URL=https://wiki.kkuu.de
LEAFWIKI_ADMIN_USERNAME=agent-provisioner
LEAFWIKI_ADMIN_PASSWORD=专用管理员随机密码

AGENT_ENROLL_BIND=127.0.0.1
AGENT_ENROLL_PORT=8787
AGENT_ENROLL_CODES_FILE=/var/lib/leafwiki-enroll/enrollment-codes.json
AGENT_ENROLL_REGISTRY_DIR=/var/lib/leafwiki-enroll/agent-keys
AGENT_ENROLL_ALLOWED_IPS=127.0.0.1
AGENT_ENROLL_TRUST_PROXY=false
AGENT_ENROLL_IP_LIMIT=10
AGENT_ENROLL_GLOBAL_LIMIT=100
```

权限必须是 `600`：

```bash
chown root:root /etc/leafwiki-agent/enroll.env
chmod 0600 /etc/leafwiki-agent/enroll.env
```

### 3.5 安装 systemd 服务

```bash
install -m 0644 /opt/leafwiki-agent/leafwiki-enroll.service.example \
  /etc/systemd/system/leafwiki-enroll.service
systemctl daemon-reload
systemctl enable --now leafwiki-enroll
```

检查：

```bash
systemctl --no-pager status leafwiki-enroll
curl -fsS http://127.0.0.1:8787/health
```

期望：

```json
{"status": "ok"}
```

如果 Python 不在 `/usr/bin/python3`，修改 service 文件里的 `ExecStart`。

### 3.6 首次连通性检查

服务启动后，管理员应当实际登录一次 LeafWiki 并确认服务能创建用户和 Key。  
先不要发放真实注册码，使用下面的“无效注册码”检查网络和接口：

```bash
curl -sS -o /tmp/enroll-check.json -w '%{http_code}\n' \
  -X POST http://127.0.0.1:8787/register \
  -H 'Content-Type: application/json' \
  -d '{"code":"invalid-test-code","agentName":"connectivity-check"}'
cat /tmp/enroll-check.json
```

期望 HTTP 状态码是 `401`，而不是 `000`、`502` 或 `500`。  
这表示注册服务已经启动，并且请求格式正确。

## 4. 公网接入方式

默认注册服务只监听 `127.0.0.1:8787`，**不要直接把 8787 端口裸暴露到公网**。  
远程 Agent 选下面一种方式接入。

### 方式 A：SSH 隧道，最推荐

适合 Agent 能通过 SSH 登录服务器，或者能通过跳板机访问服务器的情况。

Agent 机器执行：

```bash
ssh -N -L 8787:127.0.0.1:8787 root@wiki.kkuu.de
```

然后 Agent 使用：

```text
http://127.0.0.1:8787/register
```

注册完成后可以关闭隧道。  
SSH 隧道不新增公网端口，也不需要改 Nginx。

注意：所有经隧道来的请求在服务端看起来都来自 `127.0.0.1`，所以默认每个 IP 每小时 10 次的限制会变成所有 Agent 共享。  
如果一次要接入超过 10 个 Agent，可以把 `AGENT_ENROLL_IP_LIMIT` 调到例如 `50` 后重启服务。

### 方式 B：Nginx 单独路径

适合 Agent 无法使用 SSH、但能访问 `https://wiki.kkuu.de` 的情况。

把 `nginx-agent-enroll.conf.example` 中的 `limit_req_zone` 放到 Nginx 的 `http { ... }` 上下文，把 `location` 放进已经存在的 `wiki.kkuu.de` 的 `server { ... }` 中。  
不要为同一个域名再创建一个重复的 server 块。

核心配置：

```nginx
limit_req_zone $binary_remote_addr zone=leafwiki_enroll:10m rate=10r/m;

location = /agent-enroll/register {
    limit_req zone=leafwiki_enroll burst=5 nodelay;
    proxy_pass http://127.0.0.1:8787/register;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

检查并重载：

```bash
nginx -t
systemctl reload nginx
```

同时把环境文件改成：

```dotenv
AGENT_ENROLL_TRUST_PROXY=true
AGENT_ENROLL_ALLOWED_IPS=
```

然后：

```bash
systemctl restart leafwiki-enroll
```

Agent 使用：

```text
https://wiki.kkuu.de/agent-enroll/register
```

安全要求：

- 必须使用 HTTPS。
- 注册码仍然是主要认证手段；不要因为用了 Nginx 就发放长期注册码。
- 如果 Agent 出口 IP 固定，建议在 Nginx 加 `allow` / `deny`，或把 `AGENT_ENROLL_ALLOWED_IPS` 设成固定网段。
- 不要把 `/agent-enroll/register` 加到公开导航、文档索引或搜索引擎。
- 先检查 Nginx 的 `location /` 不会截获这个精确路径。

### 方式 C：私有网络

如果服务器和 Agent 都在 Tailscale、WireGuard 或其他私有网络，可以把注册服务绑定到私有地址，并只允许该网段访问。  
这比公网暴露更安全。

## 5. 发放一次性注册码

### 5.1 发放只读注册码

```bash
sudo -u leafwiki-enroll python3 \
  /opt/leafwiki-agent/issue_enrollment_codes.py \
  --role viewer \
  --count 10 \
  --code-expires-days 7 \
  --key-expires-days 90 \
  --codes-file /var/lib/leafwiki-enroll/enrollment-codes.json \
  --output-file /var/lib/leafwiki-enroll/issued-viewer-20261118.json
```

### 5.2 发放可写注册码

```bash
sudo -u leafwiki-enroll python3 \
  /opt/leafwiki-agent/issue_enrollment_codes.py \
  --role editor \
  --count 2 \
  --code-expires-days 7 \
  --key-expires-days 90 \
  --codes-file /var/lib/leafwiki-enroll/enrollment-codes.json \
  --output-file /var/lib/leafwiki-enroll/issued-editor-20261118.json
```

参数含义：

| 参数 | 说明 |
|---|---|
| `--role viewer` | 注册后只能读 |
| `--role editor` | 注册后可以读写 |
| `--count` | 本次生成多少个注册码 |
| `--code-expires-days` | 注册码有效天数，建议 7 天，范围 1-30 |
| `--key-expires-days` | 注册后 API Key 有效天数，建议 90 天；`0` 表示不过期 |

注册码文件权限应为 `600`。  
不要重复使用同一个注册码；一个注册码只对应一次注册。

查看待发放的注册码：

```bash
sudo -u leafwiki-enroll cat \
  /var/lib/leafwiki-enroll/issued-viewer-20261118.json
```

这是敏感文件。只通过私密渠道发给对应 Agent，不要贴到公开群、Git 或公开工单。

## 6. Agent 自助注册

### 6.1 Agent 拿到什么

每个 Agent 只需要拿到：

```text
注册服务地址
一个未使用的一次性注册码
自己的唯一 Agent 名称
```

例如：

```text
注册地址：http://127.0.0.1:8787/register
注册码：lw_enroll_xxxxxxxxx
Agent 名称：codex-reader-01
```

Agent 名称建议：

```text
codex-reader-01
claude-reader-01
research-reader-01
codex-editor-01
```

名称只能由字母、数字、点、下划线、短横线组成，长度 3-64 个字符。  
不要用中文、空格或邮箱作为用户名。

### 6.2 执行注册

Agent 机器需要 `register_agent.py`。  
如果 Agent 能访问服务端，也可以让管理员把该文件放到 Agent 的工作目录。

SSH 隧道模式：

```bash
python3 register_agent.py \
  --server http://127.0.0.1:8787/register \
  --code '把一次性注册码放在这里' \
  --agent-name codex-reader-01 \
  --output-dir "$HOME/.leafwiki/agent-keys"
```

Nginx 模式：

```bash
python3 register_agent.py \
  --server https://wiki.kkuu.de/agent-enroll/register \
  --code '把一次性注册码放在这里' \
  --agent-name codex-reader-01 \
  --output-dir "$HOME/.leafwiki/agent-keys"
```

成功后输出类似：

```text
saved credentials to /home/agent/.leafwiki/agent-keys/codex-reader-01.json
agent=codex-reader-01 role=viewer expires=2027-02-16T00:00:00Z
The API key is stored in the file and is not printed.
```

凭据文件：

```json
{
  "baseUrl": "https://wiki.kkuu.de",
  "agentUsername": "codex-reader-01",
  "agentEmail": "codex-reader-01@agents.local",
  "role": "viewer",
  "memoryRoot": "agents/codex-reader-01/",
  "memoryPolicyVersion": "1.0",
  "keyName": "codex-reader-01-20261118",
  "expiresAt": "2027-02-16T00:00:00Z",
  "apiKeyId": "key-id",
  "apiKey": "lw_xxx"
}
```

要求：

```bash
chmod 0700 "$HOME/.leafwiki/agent-keys"
chmod 0600 "$HOME/.leafwiki/agent-keys/"*.json
```

不要把 `apiKey` 打印到终端、日志、最终回答或聊天记录。

### 6.3 使用知识库

设置环境变量：

```bash
export LEAFWIKI_BASE_URL=https://wiki.kkuu.de
export LEAFWIKI_API_KEY_FILE="$HOME/.leafwiki/agent-keys/codex-reader-01.json"
```

每次启动先检查凭据：

```bash
python3 leafwiki_agent.py auth-check
```

期望：

```text
AUTH_KEY_VALID
```

如果返回 `AUTH_KEY_EXPIRED`，停止操作并让管理员为同一个 Agent 用户轮换 Key。  
不要重新注册、不要创建第二个用户、不要猜新 Key。

只读 Agent 验证：

```bash
python3 leafwiki_agent.py tree
python3 leafwiki_agent.py search "部署" --limit 5
python3 leafwiki_agent.py read "docs/部署"
```

需要结构化输出时加 `--json`：

```bash
python3 leafwiki_agent.py tree --json
python3 leafwiki_agent.py search "部署" --json
python3 leafwiki_agent.py read "docs/部署" --json
```

只读 Agent 测试写入边界：

```bash
python3 leafwiki_agent.py create \
  --title "viewer-should-not-write" \
  --slug "viewer-should-not-write" \
  --parent-id root \
  --content "test"
```

期望返回 `403`，并且知识库不应该多出这个页面。

### 6.4 可写 Agent 验证

可写 Agent 使用 `editor` 注册码注册后，额外验证：

```bash
python3 leafwiki_agent.py create \
  --title "Agent Smoke Test" \
  --slug "agent-smoke-test-codex-editor-01" \
  --parent-id root \
  --content "created by codex-editor-01"

python3 leafwiki_agent.py read "agent-smoke-test-codex-editor-01"

python3 leafwiki_agent.py append \
  "agent-smoke-test-codex-editor-01" \
  --text "append test ok"
```

`--parent-id root` 表示放在根目录。  
如果要放到某个父页面下，必须传父页面的真实 ID，不是页面路径；可以先执行 `tree --json` 查找。

## 7. 服务端如何知道谁接入了

不需要提前维护清单。注册成功后，服务端会在本地登记：

```bash
sudo -u leafwiki-enroll ls -l /var/lib/leafwiki-enroll/agent-keys/
```

只显示账号、角色和 Key 编号，不打印 Key：

```bash
sudo -u leafwiki-enroll python3 - <<'PY'
import json
from pathlib import Path

root = Path("/var/lib/leafwiki-enroll/agent-keys")
for path in sorted(root.glob("*.json")):
    data = json.loads(path.read_text(encoding="utf-8"))
    print(
        data.get("agentUsername"),
        data.get("role"),
        data.get("apiKeyId"),
        data.get("expiresAt"),
    )
PY
```

也可以在 LeafWiki 管理页面的“用户”和“API 密钥”中看到实际账号和 Key。

## 8. 撤销、轮换和过期

### 8.1 撤销

管理员进入 LeafWiki 网页的“设置 → API 密钥”，找到对应 Key 并撤销。  
撤销立即生效，不需要重启容器。  
只撤销一个 Key 不会影响其他 Agent。

如果要彻底停用某个 Agent，先撤销 Key，再在用户管理里删除该用户。

### 8.2 Key 过期

Key 到期后，Agent 请求会返回 `401`。  
处理方式：

1. 管理员为该用户创建一个新 Key。
2. 把新凭据文件通过私密渠道交给 Agent。
3. Agent 替换本地凭据后重新验证。

可以使用 `create_agent_key.py` 为已存在的用户创建新 Key：

```bash
set -a
. /etc/leafwiki-agent/enroll.env
set +a

python3 /opt/leafwiki-agent/create_agent_key.py \
  --agent-username codex-reader-01 \
  --agent-email codex-reader-01@agents.local \
  --role viewer \
  --key-name codex-reader-01-20270216 \
  --expires-days 90 \
  --output-file /var/lib/leafwiki-enroll/agent-keys/codex-reader-01-20270216.json
```

注意：`create_agent_key.py` 会输出凭据文件，不会自动把旧 Key 撤销。  
完成切换后，必须回到网页撤销旧 Key。

### 8.3 注册码过期或已使用

重新发一个新注册码即可。  
已经用过的注册码不能恢复。

## 9. 安全要求

- 注册服务默认只监听 `127.0.0.1`，不要裸暴露 `8787`。
- 公网只通过 SSH 隧道、Nginx 精确路径或私有网络接入。
- 使用专用管理员账号 `agent-provisioner`，不要使用个人主管理员账号。
- 专用管理员账号关闭 TOTP；这是无人值守注册服务的限制。
- 注册码一次性、默认 7 天过期，只通过私密渠道发放。
- 每个 Agent 一个用户、一个 Key，不共享 Key。
- 不创建 `admin` API Key。
- API Key 只写入权限 `600` 的文件，不打印、不写 Git。
- 注册服务日志不得包含请求体、注册码或完整 Key。
- `/var/lib/leafwiki-enroll` 和 `agent-keys` 目录权限 `700`。
- Nginx 使用 HTTPS，并限制请求体大小和频率。
- 发现注册码泄露，等待其过期或删除对应注册码条目；如果已经注册，撤销生成的 Key。
- 发现管理员密码泄露，立即修改 `agent-provisioner` 密码并重启注册服务。

## 10. 验收清单

- [ ] `enableApiKeyManagement` 仍为 `true`。
- [ ] `/opt/leafwiki-agent` 中脚本语法检查通过。
- [ ] `leafwiki-enroll` 服务状态为 `active (running)`。
- [ ] `curl http://127.0.0.1:8787/health` 返回 `{"status": "ok"}`。
- [ ] 无效注册码返回 `401`，不是 `502`。
- [ ] 注册服务只监听 `127.0.0.1:8787`。
- [ ] 环境文件权限为 `600`。
- [ ] 注册码文件和凭据目录权限为 `600/700`。
- [ ] 第一个 viewer 注册码成功创建用户和 Key。
- [ ] 同一个注册码第二次使用被拒绝。
- [ ] 过期注册码返回 `410`。
- [ ] viewer Agent 的 `tree/search/read` 成功。
- [ ] viewer Agent 写入返回 `403`。
- [ ] editor Agent 能创建、读取、追加测试页面。
- [ ] 凭据文件权限为 `600`。
- [ ] 服务端登记目录中能看到该 Agent。
- [ ] 撤销 Key 后该 Agent 返回 `401`，其他 Agent 不受影响。
- [ ] 日志、终端和交接文档中没有完整 API Key。

## 11. 故障排查

### 注册服务启动失败

```bash
systemctl --no-pager status leafwiki-enroll
journalctl -u leafwiki-enroll -n 100 --no-pager
```

重点检查：

- `LEAFWIKI_ADMIN_USERNAME` / `LEAFWIKI_ADMIN_PASSWORD` 是否填写。
- 管理员账号是否是 `admin` 角色。
- 管理员账号是否启用了 TOTP；注册服务当前不支持交互式 TOTP。
- `/var/lib/leafwiki-enroll` 是否可写。
- `python3` 路径是否与 service 文件一致。

### `401 invalid enrollment code`

- 注册码是否复制完整。
- 注册码是否已经被使用。
- 是否把 `viewer` 码和 `editor` 码混用。
- 是否把注册码文件中的 JSON 引号也复制进去了；只复制 `code` 字段的值。

### `409 agent user already exists`

Agent 名称已被占用。  
让该 Agent 换一个唯一名称；如果这是原有 Agent 的 Key 轮换，请不要重复注册，使用 `create_agent_key.py` 创建新 Key。

### `410 enrollment code expired`

注册码已超过有效期。重新发放新码。

### `403 IP not allowed`

请求来源 IP 不在 `AGENT_ENROLL_ALLOWED_IPS` 中。

- SSH 隧道：通常应保留 `AGENT_ENROLL_ALLOWED_IPS=127.0.0.1`。
- Nginx：使用 `AGENT_ENROLL_TRUST_PROXY=true` 并设置合适的 `AGENT_ENROLL_ALLOWED_IPS`，或者留空表示不限制 IP。

### `429 too many enrollment attempts`

触发了限流。

- 等一小时后重试。
- SSH 隧道多 Agent 场景可提高 `AGENT_ENROLL_IP_LIMIT`。
- 检查是否有 Agent 在循环重试错误注册码。

### Nginx 返回 `404`

`/agent-enroll/register` 没有匹配到精确 location。  
用 `nginx -T` 检查最终配置，确认 `location = /agent-enroll/register` 在正确的 server 块内。

### Nginx 返回 `502`

注册服务没运行，或 `proxy_pass` 端口不是 `127.0.0.1:8787`：

```bash
systemctl status leafwiki-enroll
ss -lntp | grep 8787
```

### 注册成功但 CLI 返回 `401`

- 凭据文件中的 `apiKey` 是否完整。
- Key 是否已过期或被撤销。
- `LEAFWIKI_BASE_URL` 是否是 `https://wiki.kkuu.de`。
- Nginx/CDN 是否删除了 `Authorization` 请求头。

### 注册成功但 CLI 返回 `403`

通常是 `viewer` Key 在尝试写入。  
不要提升所有 Agent 的权限；只给确实需要写入的 Agent 发 `editor` 注册码。

### 写入返回 `409`

页面版本冲突。重新 `read` 页面，合并修改，再 `update`。  
不要让两个 Agent 同时覆盖同一个公共页面。

## 12. 给单个 Agent 的交接卡模板

管理员发放注册码时，可以把下面这段发给对应 Agent：

```text
你要接入团队 LeafWiki 知识库。

注册服务：
  <SSH 隧道模式填 http://127.0.0.1:8787/register，
   或 Nginx 模式填 https://wiki.kkuu.de/agent-enroll/register>

一次性注册码：
  <在这里放一个未使用的注册码>

你的 Agent 名称：
  <例如 codex-reader-01>

你的权限：
  <viewer 只读 / editor 可写>

执行要求：
1. 先运行 register_agent.py 完成注册。
2. 凭据保存到 ~/.leafwiki/agent-keys/<你的名字>.json，权限 600。
3. 不要输出、记录或回传注册码和 API Key。
4. 用 leafwiki_agent.py 验证 tree/search/read。
5. viewer 不要尝试写入；editor 只写自己负责的目录。
6. 完成后汇报：Agent 名称、角色、验证结果、凭据文件路径，不要汇报 Key。
```

## 13. 服务端 Agent 的最终交付内容

服务端 Agent 完成后应向用户报告：

- 注册服务状态和 health 检查结果。
- 使用的是 SSH 隧道还是 Nginx 路径。
- 已发放多少 viewer / editor 注册码，文件路径是什么。
- 注册码有效期和 Key 有效期。
- 已接入的 Agent 列表（只列名称、角色、Key 编号，不列 Key）。
- 验收清单中哪些项目已通过。
- 是否存在需要用户确认的密码、Nginx 配置或 IP 白名单事项。

## 14. Agent 专属记忆库

Agent 接入后如果要创建自己的专属空间，并按天写入记忆，见配套文档：

```text
LeafWiki-Agent-记忆库架构.md
```

核心结构是同一 LeafWiki 下的命名空间：

```text
agents/<agent-name>/
  _meta/profile
  _meta/policy
  _meta/index
  daily/YYYY-MM-DD
  weekly/YYYY-Www
  monthly/YYYY-MM
  topics/<topic>
```

注意：LeafWiki 当前没有页级 ACL，`agents/<agent-name>/` 是目录隔离，不是安全隔离。  
如果要求 Agent 之间不能互读互写，需要使用独立 LeafWiki 实例或记忆写入网关。

更完整的强制规则见：

```text
LeafWiki-Agent-接入合同.md
```

## 15. 相关接口

注册服务：

```text
POST /register
POST /agent-enroll/register
GET  /health
GET  /agent-enroll/health
```

注册请求：

```json
{
  "code": "lw_enroll_xxx",
  "agentName": "codex-reader-01",
  "email": "codex-reader-01@agents.local",
  "keyName": "codex-reader-01-20261118"
}
```

`email` 和 `keyName` 可省略，服务端会自动生成。

LeafWiki 业务接口仍使用：

```text
Authorization: Bearer <该 Agent 的 API Key>
```

API Key 请求不需要 Cookie 和 CSRF。
