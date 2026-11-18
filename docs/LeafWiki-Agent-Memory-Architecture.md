# LeafWiki Agent 专属记忆库架构

> 适用场景：多个 Codex / Claude / 自动化 Agent 共用 `https://wiki.kkuu.de`  
> 目标：每个 Agent 有专属空间，按天写入新记忆，之后能按主题和历史召回  
> 前提：API Key 已通过一次性注册码自助接入

## 1. 先明确边界

LeafWiki 当前只有三种全局角色：

```text
admin    管理员
editor   可读写全部页面
viewer   只读全部页面
```

它当前没有“页级权限”或“每个用户只能读写自己的目录”的 ACL。

因此：

- “每个 Agent 一个专属库”应理解为 **同一 LeafWiki 内的专属命名空间**。
- 它适合受信任的内部 Agent，能避免内容混在一起，但不能阻止一个 editor Agent 技术上写入别人的目录。
- 如果要求 Agent 之间绝对不能互读、互写，必须使用下面两种方案之一：
  1. 每个 Agent 一个独立 LeafWiki 实例，独立数据目录和子域名；
  2. 增加一个记忆写入网关，Agent 只有 viewer Key，由网关校验路径后用唯一的 editor Key 写入。

本文默认采用“受信任团队 + 命名空间 + 写入规范”的方案，成本最低，适合当前部署。

## 2. 推荐目录结构

注册响应现在会下发：

```text
memoryRoot: agents/<agent-name>/
memoryPolicyVersion: 1.0
```

Agent 必须以注册响应为准，不得自行猜目录。  
本地凭据文件中保存这两个字段，后续写入统一由客户端做路径校验。

不要为每个 Agent 建一个独立顶层根，否则页面树会越来越乱。  
统一放在一个 `agents` 根下面：

```text
agents/
  codex-editor-01/
    _meta/
      profile
      policy
      index
    daily/
      2026-11-18
      2026-11-19
      2026-11-20
    weekly/
      2026-W46
      2026-W47
    monthly/
      2026-11
    topics/
      deployment
      incidents
      preferences
      project-alpha
    inbox/
      unverified-notes
```

类型约定：

| 路径 | 类型 | 作用 |
|---|---|---|
| `agents` | section | 所有 Agent 空间的根 |
| `agents/<agent-name>` | section | 某个 Agent 的专属空间 |
| `_meta/profile` | page | 该 Agent 的身份、职责、能力边界 |
| `_meta/policy` | page | 记忆写入规则、隐私规则、目录规则 |
| `_meta/index` | page | 重要主题、最近记忆、待办索引 |
| `daily/YYYY-MM-DD` | page | 当天追加式记忆流水 |
| `weekly/YYYY-Www` | page | 每周压缩总结 |
| `monthly/YYYY-MM` | page | 每月归档总结 |
| `topics/<topic>` | page | 长期稳定的语义记忆 |
| `inbox/unverified-notes` | page | 未确认、待归档的临时记忆 |

示例：

```text
agents/codex-editor-01/daily/2026-11-18
agents/codex-editor-01/topics/deployment
agents/codex-editor-01/_meta/index
```

## 3. 记忆分三层

不要把所有内容都堆进每日页面。推荐分成三层：

### 3.1 每日流水，episodic memory

记录“今天发生了什么”：

- 完成了什么任务
- 做了什么决定
- 遇到什么错误
- 用户提出了什么偏好
- 还有什么没有完成

特点：追加式、按日期、允许不完美，作为原始记忆。

### 3.2 主题页，semantic memory

记录“长期有效的事实和结论”：

- 部署流程
- 项目背景
- 常用命令
- 用户偏好
- 已知故障和处理方式

特点：人工或 Agent 整理过的稳定知识，不按天重复。

### 3.3 索引与画像，working memory

记录“下次启动时先看什么”：

- Agent 的职责和边界
- 最近 7 天的重要变化
- 重要主题入口
- 未完成任务
- 需要用户确认的事项

特点：短、稳定、每次启动优先读取。

## 4. 专属库初始化

Agent 第一次注册成功后，创建一次专属空间。初始化必须幂等，重复执行不能生成重复目录。

每次启动先验证 Key：

```bash
python3 /opt/leafwiki-agent/leafwiki_agent.py auth-check
```

`expired` 或 `revoked_or_invalid` 时停止，不要重新注册或猜新 Key，  
让管理员给同一个 `agentUsername` 轮换 Key；`memoryRoot` 保持不变。

推荐初始化内容：

```text
agents/
  <agent-name>/
    _meta/profile      我的职责、权限、可写范围
    _meta/policy       记忆写入与隐私规则
    _meta/index        主题和最近记忆入口
    daily/             每日记忆
    weekly/            每周总结
    monthly/           每月总结
    topics/            长期主题
    inbox/             待确认内容
```

### 4.1 页面创建顺序

使用 LeafWiki API 时，父页面必须通过 `parentId` 指定，不能用路径直接当父 ID：

```text
1. POST /api/pages  创建 agents section，parentId = null
2. POST /api/pages  创建 agents/<agent-name> section
3. POST /api/pages  创建 _meta section
4. POST /api/pages  创建 profile / policy / index page
5. POST /api/pages  创建 daily / weekly / monthly / topics / inbox section
```

创建后可以用：

```text
GET /api/pages/by-path?path=agents/<agent-name>/_meta/profile
```

读取页面并拿到真实 `id` 和 `version`。

### 4.2 初始化检查

```text
agents 不存在 -> 创建
agents/<agent-name> 不存在 -> 创建
_meta、daily、weekly、monthly、topics、inbox 不存在 -> 创建
profile、policy、index 不存在 -> 创建
已存在 -> 复用，不覆盖
```

注意：不要每次启动都重建页面，也不要用空内容覆盖已有 profile。

## 5. 每日记忆写入

### 5.1 写入时机

不要每收到一条消息就写一次。推荐在以下时机写：

- 完成一个任务后
- 做出一个可复用决定后
- 学到用户长期偏好后
- 遇到并解决一个值得复用的错误后
- 当天结束前的收尾

每次只写“值得下次记住的内容”，不要保存完整聊天记录。

### 5.2 每日页面

每个 Agent 每天一个页面：

```text
agents/<agent-name>/daily/YYYY-MM-DD
```

第一次写入时创建，后续当天都追加到同一页。  
日期使用服务器或用户所在时区，当前建议 `Asia/Shanghai`。

### 5.3 每条记忆的格式

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

字段可以简化，但建议保留：

| 字段 | 说明 |
|---|---|
| `memory_id` | 幂等标识，防止重复写入 |
| `type` | `decision` / `fact` / `task` / `preference` / `error` / `runbook` |
| `context` | 当时为什么发生 |
| `action` | 做了什么 |
| `result` | 结果是什么 |
| `follow_up` | 下次要做什么 |
| `confidence` | `high` / `medium` / `low` |

### 5.4 幂等

写入前先用 `memory_id` 搜索或读取当天页面：

```text
如果 memory_id 已存在 -> 不重复追加
如果不存在 -> 追加
```

`memory_id` 推荐：

```text
<agent-name>-<YYYYMMDD>-<3位序号>
```

### 5.5 追加而不是整页覆盖

使用 LeafWiki 的“读取当前页面 + 追加内容 + 按当前 version 更新”流程：

```text
1. GET /api/pages/by-path?path=agents/<agent>/daily/YYYY-MM-DD
2. 如果 404，创建当天页面
3. 在 Markdown 末尾追加新条目
4. PUT /api/pages/{id}，带上最新 version
5. 如果 409，重新读取，重新追加，最多重试 3 次
```

不要在旧 `version` 上盲目覆盖。  
同一个 Agent 的同一天页面，建议只允许一个写入进程；如果有多个进程，改为“每条记忆一个页面”：

```text
agents/<agent>/daily/2026-11-18/1432-decision-deploy
agents/<agent>/daily/2026-11-18/1505-error-nginx
```

## 6. 主题页更新

每日页面是流水，不是最终知识。  
当某条记忆是长期有效的事实时，更新主题页：

```text
agents/<agent-name>/topics/deployment
agents/<agent-name>/topics/preferences
agents/<agent-name>/topics/incidents
```

更新原则：

- 新事实覆盖旧事实时，在主题页写“当前结论”，并在每日页保留变更历史。
- 不确定的信息先放 `inbox`，不要直接写进主题页。
- 主题页保持短小，详细证据链接回每日页。

## 7. Agent 启动时的召回流程

每次 Agent 启动或接到新任务时，按下面顺序读取：

```text
1. agents/<agent>/_meta/profile
2. agents/<agent>/_meta/policy
3. agents/<agent>/_meta/index
4. agents/<agent>/daily/最近 1-3 天
5. 用 tags 搜索相关主题
6. 只读取命中主题的 topics 页面
```

推荐搜索：

```text
GET /api/search?q=<任务关键词>&tags=agent:<agent-name>&tags=memory:daily&limit=20
```

如果只想看全部记忆索引：

```text
GET /api/search?tags=agent:<agent-name>&tags=memory:daily&limit=50
```

不要每次把所有每日页面全读一遍。  
每日页是历史流水，主题页和索引才是高频入口。

## 8. 每周和每月压缩

### 8.1 每周

每周日或周一执行：

```text
读取 agents/<agent>/daily/ 最近 7 天
  -> 生成 agents/<agent>/weekly/YYYY-Www
  -> 更新 topics/ 中稳定事实
  -> 更新 _meta/index 的最近重要变化
```

周总结只写：

- 本周完成
- 本周重要决定
- 本周新增/变更的长期事实
- 未解决问题
- 下周需要注意

### 8.2 每月

每月执行一次，把周总结压缩成：

- 本月主线
- 已稳定的事实
- 已废弃的旧结论
- 长期未解决事项
- 需要人工确认的内容

不要删除原始每日页，至少保留一段时间用于追溯。

## 9. 标签约定

建议统一使用这些标签，方便搜索和过滤：

```text
agent:<agent-name>
memory:daily
memory:weekly
memory:monthly
type:decision
type:fact
type:task
type:preference
type:error
type:runbook
visibility:team
visibility:private
```

示例：

```text
agent:codex-editor-01
memory:daily
type:decision
visibility:team
```

注意：`visibility:private` 只是内容约定，不是 LeafWiki 的权限控制。  
真正机密内容不要放进共享 LeafWiki。

## 10. 写入质量规则

应写入：

- 用户明确的长期偏好
- 可复用的决策和理由
- 反复出现的错误及解决方法
- 项目事实、路径、约定
- 未完成任务和下一步

不应写入：

- 完整聊天记录
- 临时寒暄和重复内容
- 密码、Token、API Key、私钥
- 身份证号、银行卡号等敏感个人信息
- 未经验证的猜测，除非明确标记为 `low confidence` 并放入 `inbox`

如果必须记录敏感信息，只记录“去哪里找”或“已配置”，不要记录值本身。

## 11. 权限与安全建议

### 11.1 受信任团队

每个需要写记忆的 Agent：

```text
角色：editor
Key：独立
目录规范：只写 agents/<自己的名字>/
```

这种方式靠 Agent 的提示词和客户端封装约束，不是硬隔离。

### 11.2 需要硬隔离

如果 Agent 之间不能互读：

```text
方案 A：每个 Agent 一个 LeafWiki 实例
  - 独立域名
  - 独立数据目录
  - 独立备份
  - 成本最高，隔离最好

方案 B：记忆写入网关
  - Agent 只有 viewer Key
  - Agent 把记忆提交给网关
  - 网关校验 agent-name 和路径
  - 网关用唯一的 editor Key 写入
  - 读取也由网关按 agent 过滤
```

当前这套一次性注册码方案不提供页级 ACL，不要把它宣传成“Agent 之间完全隔离”。

## 12. 服务端 Agent 落地步骤

1. 先完成 `LeafWiki-Agent自助接入教程.md` 中的注册服务。
2. 为需要记忆的 Agent 发 `editor` 注册码。
3. Agent 注册后拿到独立 Key。
4. 首次运行初始化 `agents/<agent-name>/` 目录骨架。
5. 每次任务结束调用每日写入。
6. 每周执行压缩任务，更新主题页和索引。
7. 用验收清单验证读取、追加、冲突和隔离边界。

## 13. 验收清单

- [ ] `agents/<agent-name>/` 已创建且不重复。
- [ ] `_meta/profile`、`_meta/policy`、`_meta/index` 已存在。
- [ ] `daily/YYYY-MM-DD` 当天页面可创建。
- [ ] 同一天第二条记忆能追加，不覆盖第一条。
- [ ] 相同 `memory_id` 重复写入不会重复追加。
- [ ] 写入使用最新 `version`，409 能重新读取后重试。
- [ ] 上周/上月总结页能生成。
- [ ] 主题页只保留稳定结论，不堆积流水。
- [ ] Agent 启动时只读取 profile、policy、index、最近几天和相关主题。
- [ ] 没把 API Key、密码、Token 写进记忆。
- [ ] 明确记录“命名空间不是权限隔离”这一限制。

## 14. 推荐的第一版

如果现在就要上线，不要一开始做复杂系统。先做最小闭环：

```text
每个 Agent：
  agents/<agent-name>/
    _meta/profile
    _meta/index
    daily/YYYY-MM-DD
    topics/
```

先把“每日追加 + 启动读取 + 每周总结”跑通，再增加 monthly、inbox、自动索引和写入网关。
