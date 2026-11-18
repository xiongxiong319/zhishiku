# LeafWiki Agent 接入合同

> 合同版本：`1.0`  
> 适用范围：所有通过一次性注册码接入 `https://wiki.kkuu.de` 的 Agent  
> 目的：让未来 Agent 不依赖口头约定，按固定目录、固定格式、固定校验执行  
> 配套：`LeafWiki-Agent自助接入教程.md`、`LeafWiki-Agent-记忆库架构.md`

## 1. 合同原则

后续 Agent 不直接“自由发挥”地调用 LeafWiki。  
接入合同分四层执行：

```text
注册时下发硬约束
  -> 统一客户端做路径守卫
  -> policy 页面和系统提示词锁定行为
  -> 服务端周期审计和撤销
```

提示词只是其中一层，不能单独承担隔离责任。

## 2. 注册响应必须带合同字段

注册服务创建 Agent 后，返回和本地凭据文件中应包含：

```json
{
  "baseUrl": "https://wiki.kkuu.de",
  "agentUsername": "codex-editor-01",
  "role": "editor",
  "memoryRoot": "agents/codex-editor-01/",
  "memoryPolicyVersion": "1.0",
  "expiresAt": "2027-02-16T00:00:00Z",
  "apiKeyId": "key-id",
  "apiKey": "lw_xxx"
}
```

其中：

- `memoryRoot` 是唯一允许写入的根路径。
- `memoryPolicyVersion` 是 Agent 必须遵守的合同版本。
- Agent 不得自行修改这两个字段。
- 如果本地合同版本与服务端不一致，停止写入并报告。

## 2.1 Key 生命周期与默认行为

凭据文件包含 `expiresAt`。  
每次 Agent 启动或开始新任务前，必须先执行：

```bash
python3 /opt/leafwiki-agent/leafwiki_agent.py auth-check
```

### 状态定义

| 状态 | 含义 | 默认行为 |
|---|---|---|
| `valid` | Key 有效，距过期超过 7 天 | 正常读写 |
| `expiring_soon` | 7 天内过期 | 可以继续必要工作，但必须提醒管理员轮换 |
| `expired` | 已过期 | 停止工作和写入，报告管理员轮换 |
| `revoked_or_invalid` | 被撤销、输错或不属于当前站点 | 停止，报告管理员轮换 |
| `valid_expiry_unknown` | Key 可用但缺少 `expiresAt` | 只读可用；写入前要求管理员确认有效期 |

### 401 的处理

收到 HTTP `401` 时，Agent 必须：

1. 停止当前操作。
2. 输出结构化错误码：`AUTH_KEY_EXPIRED` 或 `AUTH_KEY_REVOKED_OR_INVALID`。
3. 报告凭据文件路径、`agentUsername`、`apiKeyId` 和 `expiresAt`。
4. 请管理员为**同一个 Agent 用户**创建新 Key。

Agent 禁止：

- 再次使用新注册码注册同一个 Agent；
- 创建第二个用户；
- 猜测或手工拼接新 Key；
- 切换到其他 Agent 的 Key；
- 修改 `memoryRoot`；
- 在 401 后无限重试；
- 把 401 当成 409 或 429 处理。

### 轮换后的行为

管理员创建新 Key 后，Agent 只替换凭据文件：

```text
agentUsername 不变
memoryRoot 不变
apiKey 和 apiKeyId 改变
```

记忆页面不会迁移，也不会新建第二个记忆库。  
Agent 继续写入原来的 `daily/`、`topics/` 和 `_meta/`。

## 3. 强制目录结构

每个 Agent 只能写入自己的 `memoryRoot`：

```text
agents/<agent-name>/
  _meta/
    profile
    policy
    index
  daily/
    YYYY-MM-DD
  weekly/
    YYYY-Www
  monthly/
    YYYY-MM
  topics/
    <topic>
  inbox/
    unverified-notes
```

强制规则：

1. 不允许在 `agents/` 根下直接写页面。
2. 不允许写入其他 Agent 的 `agents/<other-agent>/`。
3. 不允许把每日流水写进 `_meta/` 或 `topics/`。
4. 不允许在 `topics/` 中保存未验证猜测。
5. 初始化必须幂等，已存在的页面只能复用，不能空内容覆盖。

## 4. 初始化合同

Agent 首次运行时执行一次 `memory-init`。  
必须创建：

```text
agents/<agent-name>/
agents/<agent-name>/_meta/
agents/<agent-name>/_meta/profile
agents/<agent-name>/_meta/policy
agents/<agent-name>/_meta/index
agents/<agent-name>/daily/
agents/<agent-name>/weekly/
agents/<agent-name>/monthly/
agents/<agent-name>/topics/
agents/<agent-name>/inbox/
```

可选的全局合同页：

```text
agents/_contract
```

每个 Agent 的 `_meta/policy` 应包含：

```text
contract_version: 1.0
memory_root: agents/<agent-name>/
allowed_write_prefix: agents/<agent-name>/
read_before_write: true
daily_path: daily/YYYY-MM-DD
required_tags: agent:<agent-name>, memory:daily, type:<type>
forbidden: secrets, credentials, raw chat logs
auth_check: python3 /opt/leafwiki-agent/leafwiki_agent.py auth-check
on_401: stop and report AUTH_KEY_EXPIRED or AUTH_KEY_REVOKED_OR_INVALID
forbidden_on_401: re-register, new user, guess key, switch memoryRoot
```

## 5. 每日写入合同

### 5.1 写入时机

只在以下情况写入：

- 完成一个任务
- 做出一个决定
- 学到长期偏好
- 解决一个可复用错误
- 当天收尾

不要每收到一条消息就写。

### 5.2 每日页面

```text
agents/<agent-name>/daily/YYYY-MM-DD
```

当天第一次写入时创建，之后只追加。  
日期使用 `Asia/Shanghai`。

### 5.3 必填字段

每条记忆必须包含：

```text
memory_id
type
context
action 或 decision
result 或 follow_up
confidence
tags
```

示例：

```markdown
## 14:32 | decision | 部署方式确定

- memory_id: codex-editor-01-20261118-001
- type: decision
- context: 需要给多个 Agent 提供独立知识库
- decision: 使用一次性注册码 + 自助注册
- reason: 服务端不需要提前维护 Agent 清单
- follow_up: 观察注册码泄露风险
- evidence: https://wiki.kkuu.de/...
- confidence: high
- tags: agent:codex-editor-01, memory:daily, type:decision
```

`memory_id` 推荐：

```text
<agent-name>-<YYYYMMDD>-<3位序号>
```

相同 `memory_id` 不得重复写入。

### 5.4 必填标签

每条每日记忆至少带：

```text
agent:<agent-name>
memory:daily
type:<decision|fact|task|preference|error|runbook>
```

长期主题页至少带：

```text
agent:<agent-name>
memory:topic
topic:<topic>
```

### 5.5 并发和冲突

写入流程：

```text
read 当前页面
  -> 追加新条目
  -> PUT 带最新 version
  -> 409 时重新 read、重新追加
  -> 最多重试 3 次
```

同一个 Agent 的同一天页面，同一时间只允许一个写入进程。  
如果无法保证，改为每条记忆独立页面：

```text
agents/<agent-name>/daily/2026-11-18/1432-decision-deploy
```

## 6. 读取与召回合同

Agent 启动时只读：

```text
1. agents/<agent-name>/_meta/profile
2. agents/<agent-name>/_meta/policy
3. agents/<agent-name>/_meta/index
4. 最近 1-3 天 daily 页面
5. 按任务关键词和 tags 搜索
6. 只加载命中的 topics 页面
```

搜索：

```text
GET /api/search?q=<关键词>&tags=agent:<agent-name>&tags=memory:daily&limit=20
```

禁止每次启动读取整个 Wiki。

## 7. 周期压缩合同

### 每周

```text
daily/ 最近 7 天
  -> weekly/YYYY-Www
  -> 更新 topics/ 稳定事实
  -> 更新 _meta/index
```

### 每月

```text
weekly/ 本月
  -> monthly/YYYY-MM
  -> 标记废弃结论
  -> 保留长期未解决事项
```

压缩不是删除。原始 `daily` 页面保留用于追溯。

## 8. 内容安全合同

禁止写入：

- 密码、API Key、Token、私钥
- 身份证号、银行卡号、个人敏感信息
- 完整聊天记录
- 未经验证却当作事实的内容
- 其他 Agent 的私有记忆

不确定内容写入 `inbox/unverified-notes`，并标注：

```text
confidence: low
source: ...
```

## 9. 让 Agent 必须执行的四种机制

### 9.1 注册响应硬约束

注册服务已经在返回结果中加入：

```text
memoryRoot
memoryPolicyVersion
```

Agent 必须以注册响应为准，不得自行猜路径。

### 9.2 统一客户端路径守卫

不要让 Agent 直接调用 `POST /api/pages`。  
后续应统一使用 `agent_memory.py` 这样的客户端：

```text
agent_memory.py init
agent_memory.py write
agent_memory.py recall
agent_memory.py weekly-compact
```

客户端必须：

1. 从凭据文件读取 `memoryRoot` 和 `memoryPolicyVersion`。
2. 拒绝任何不匹配 `memoryRoot` 的路径。
3. 拒绝缺少必填字段的写入。
4. 自动添加必填 tags。
5. 对 409 做有限重试。
6. 阻止打印 API Key。

### 9.3 policy 页面和系统提示词

Agent 启动时必须读取：

```text
agents/<agent-name>/_meta/policy
```

如果 policy 中的 `contract_version` 与注册响应的 `memoryPolicyVersion` 不一致，停止写入并报告。

系统提示词必须写明：

```text
你只能写入注册响应给出的 memoryRoot。
不要把每日流水写入 topics。
不要把密码、Token、API Key 写入任何页面。
写入前先读取最新 version。
遇到 409 必须重新读取并合并。
```

### 9.4 服务端周期审计

建议由服务端 Agent 每日执行：

```text
1. 列出 /var/lib/leafwiki-enroll/agent-keys/*.json
2. 检查每个 Agent 是否有 agents/<name>/_meta/profile
3. 检查是否有 agents/<name>/_meta/policy
4. 检查当天是否缺少 daily/YYYY-MM-DD
5. 检查是否存在越界写入告警
6. 检查是否出现疑似 Token/API Key 字符串
7. 违规先告警，持续违规撤销 Key
```

审计告警只报告 Agent 名称、路径和问题类型，不报告完整 Key。

## 10. 合同验收清单

- [ ] 注册响应包含 `memoryRoot` 和 `memoryPolicyVersion`。
- [ ] 每个 Agent 的 `_meta/policy` 已创建。
- [ ] Agent 启动时读取 profile、policy、index。
- [ ] Agent 只能写 `agents/<agent-name>/`。
- [ ] 每日页首次创建、当天追加。
- [ ] 相同 `memory_id` 不重复。
- [ ] 409 后重新读取并合并。
- [ ] 每周生成 `weekly/YYYY-Www`。
- [ ] 长期事实进入 `topics/`，未验证内容进入 `inbox/`。
- [ ] 敏感字段扫描通过。
- [ ] 越界路径被客户端拒绝。
- [ ] 违规时能撤销 Key。

## 11. 给接入 Agent 的最小合同提示词

```text
你已通过注册服务获得 LeafWiki 凭据。

凭据文件中有：
- memoryRoot
- memoryPolicyVersion
- role

硬性规则：
1. 只能写入 memoryRoot，不得写入其他 Agent 目录。
2. 启动时先读 _meta/profile、_meta/policy、_meta/index。
3. policy 版本与 memoryPolicyVersion 不一致时停止写入。
4. 每日记忆只追加到 daily/YYYY-MM-DD。
5. 每条记忆必须有 memory_id、type、context、结果、confidence 和 tags。
6. 必须带 agent:<你的名字>、memory:daily、type:<类型> 标签。
7. 写入前读取最新 version；409 重新读取并合并，最多重试 3 次。
8. 不写密码、Token、API Key、私钥和个人敏感信息。
9. 不把每日流水塞进 topics；未验证内容放 inbox。
10. 不打印或回传 API Key。
11. 启动先运行 auth-check。
12. 401 时停止并报告；不要重新注册、不要猜新 Key、不要换 memoryRoot。
```
