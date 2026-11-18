# LeafWiki Agent 续期与记忆归属

> 适用：通过一次性注册码接入的 Agent  
> 核心结论：注册码只负责“第一次注册”，不是日常续期凭证；续期应轮换 API Key，保留原用户和原 `memoryRoot`

## 1. 三个生命周期不要混淆

| 对象 | 默认有效期 | 作用 | 到期后 |
|---|---:|---|---|
| 一次性注册码 | 7 天 | 只用于第一次注册 | 过期后重新发码；已注册的 Agent 不受影响 |
| LeafWiki 用户 | 长期 | 承载 Key 和记忆归属 | 只有管理员删除才消失 |
| Agent API Key | 90 天 | Agent 调用 LeafWiki 的凭证 | 过期后换新 Key |
| `agents/<agent-name>/` 记忆 | 长期 | 专属记忆库 | Key 换新后继续使用原目录 |

注册码过期不等于 Agent 的 Key 过期，也不等于记忆库消失。

## 2. 注册码 7 天后会发生什么

### 情况 A：注册码还没使用

7 天后注册码失效。  
处理方式：重新发一个新注册码，让 Agent 第一次注册。

```bash
sudo -u leafwiki-enroll python3 \
  /opt/leafwiki-agent/issue_enrollment_codes.py \
  --role editor \
  --count 1 \
  --code-expires-days 7 \
  --key-expires-days 90 \
  --codes-file /var/lib/leafwiki-enroll/enrollment-codes.json \
  --output-file /var/lib/leafwiki-enroll/issued-editor-renew-20261125.json
```

这不会影响其他已经注册的 Agent。

### 情况 B：注册码已经使用

注册码过期没有任何影响。  
Agent 继续使用注册时拿到的 API Key，默认还能用 90 天。

### 情况 C：Agent 的 API Key 过期或被撤销

不要重新注册同一个 Agent 名称。  
正确做法是：为原用户创建一个新 Key，保留原 `agentUsername` 和 `memoryRoot`。

## 3. 为什么不能给已注册 Agent 再发一个新注册码

当前注册服务会检查同名用户：

```text
agent-editor-01 已存在
  -> 返回 409 agent user already exists
  -> 注册码释放，不会被消耗
```

这是为了避免：

- 同一个 Agent 出现两个用户；
- 新注册把旧记忆目录变成另一套身份；
- 有人拿新注册码冒充已有 Agent。

因此：

```text
第一次接入：用注册码
后续续期：轮换原用户的 API Key
```

## 4. Key 轮换后记忆还在吗

还在。  
记忆归属看的是：

```text
agentUsername
memoryRoot
```

不是看某一把 Key。

只要新 Key 仍然属于同一个用户：

```text
agentUsername: codex-editor-01
memoryRoot: agents/codex-editor-01/
```

Agent 就会继续写入原来的：

```text
agents/codex-editor-01/daily/YYYY-MM-DD
agents/codex-editor-01/topics/...
agents/codex-editor-01/_meta/index
```

旧 Key 撤销后，旧 Key 立即失效；新 Key 继续使用同一份记忆。

## 4.1 Agent 如何知道 Key 过期

凭据文件里本来就有：

```text
expiresAt
apiKeyId
agentUsername
memoryRoot
```

Agent 每次启动先执行：

```bash
python3 /opt/leafwiki-agent/leafwiki_agent.py auth-check
```

可能结果：

| 状态 | 含义 | Agent 动作 |
|---|---|---|
| `valid` | 正常 | 继续 |
| `expiring_soon` | 7 天内过期 | 继续必要工作并提醒管理员轮换 |
| `expired` | 已过期 | 停止，报告管理员轮换 |
| `revoked_or_invalid` | 被撤销、错误或不属于当前站点 | 停止，报告管理员轮换 |
| `valid_expiry_unknown` | Key 可用但无过期字段 | 只读可用，写入前要求管理员确认 |

如果运行中收到 HTTP `401`，也按同一规则处理：

```text
停止当前操作
报告 AUTH_KEY_EXPIRED 或 AUTH_KEY_REVOKED_OR_INVALID
请管理员给同一个 agentUsername 创建新 Key
```

禁止：

- 重新注册同一个 Agent；
- 创建第二个用户；
- 猜新 Key；
- 使用其他 Agent 的 Key；
- 修改 `memoryRoot`；
- 在 401 后循环重试。

## 5. 标准续期步骤

以 `codex-editor-01` 为例。

### 5.1 创建新 Key

```bash
set -a
. /etc/leafwiki-agent/enroll.env
set +a

python3 /opt/leafwiki-agent/create_agent_key.py \
  --agent-username codex-editor-01 \
  --agent-email codex-editor-01@agents.local \
  --role editor \
  --key-name codex-editor-01-20270216 \
  --expires-days 90 \
  --output-file /var/lib/leafwiki-enroll/agent-keys/codex-editor-01-20270216.json
```

新凭据文件会包含：

```json
{
  "agentUsername": "codex-editor-01",
  "role": "editor",
  "memoryRoot": "agents/codex-editor-01/",
  "memoryPolicyVersion": "1.0",
  "keyName": "codex-editor-01-20270216",
  "expiresAt": "2027-05-17T00:00:00Z",
  "apiKeyId": "new-key-id",
  "apiKey": "lw_xxx"
}
```

### 5.2 交给 Agent 替换凭据

只替换凭据文件，不要修改 Agent 名称和 `memoryRoot`。

```bash
export LEAFWIKI_BASE_URL=https://wiki.kkuu.de
export LEAFWIKI_API_KEY_FILE="$HOME/.leafwiki/agent-keys/codex-editor-01.json"
```

如果新文件用了另一个文件名，把 `LEAFWIKI_API_KEY_FILE` 指向新文件即可。

### 5.3 验证

```bash
python3 /opt/leafwiki-agent/leafwiki_agent.py tree
python3 /opt/leafwiki-agent/leafwiki_agent.py search "记忆" --limit 5
python3 /opt/leafwiki-agent/leafwiki_agent.py read "agents/codex-editor-01/_meta/index"
```

### 5.4 撤销旧 Key

确认新 Key 可用后，在 LeafWiki 网页「设置 → API 密钥」撤销旧 Key。  
不要在未验证新 Key 前先撤销旧 Key。

## 6. 续期后的每日写作

续期后，Agent 继续按原合同写入：

```text
agents/codex-editor-01/daily/2026-11-19
agents/codex-editor-01/daily/2026-11-20
```

不会因为换 Key 而新建：

```text
agents/codex-editor-01-2/
```

也不会把历史记忆复制到新目录。

## 7. 什么情况下会产生新的记忆库

只有 `agentUsername` 变化时，才会产生新的记忆根。

例如：

```text
codex-editor-01
  -> agents/codex-editor-01/

codex-editor-02
  -> agents/codex-editor-02/
```

如果想把旧记忆迁到新 Agent：

1. 先导出旧目录内容；
2. 在新目录创建页面；
3. 保留旧目录只读；
4. 不要把旧目录直接改名，除非确认所有链接和历史引用都能接受。

## 8. 推荐策略

```text
注册码：按需发放，7 天有效，只用于第一次注册
API Key：90 天有效，到期前 7-14 天轮换
用户和 memoryRoot：长期保持稳定
记忆目录：永不因换 Key 而改名
```

续期时只做一件事：

```text
原用户 -> 新 Key -> 同一个 memoryRoot
```

不要做：

```text
原 Agent 已有用户 -> 再注册一个新用户
```
