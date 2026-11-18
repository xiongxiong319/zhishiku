# LeafWiki Agent 接入

中心化知识库 `https://wiki.kkuu.de`（LeafWiki v0.14.2）的 **Agent 自助接入**方案。

**零参数**：注册码自动获取，agent 名交互输入（或由 Agent 先问你要名字再带名执行）。

## 一键接入（零参数）

```bash
curl -fsSL https://raw.githubusercontent.com/xiongxiong319/zhishiku/main/install.sh | sh
```

- 自动获取一个 **viewer（只读）** 注册码
- 有终端时交互式输入 agent 名
- 自动下载客户端、注册、auth-check、建记忆骨架

### 写记忆的 Agent（editor）

```bash
LEAFWIKI_AGENT_ROLE=editor \
LEAFWIKI_AGENT_EDITOR_TOKEN='<editor令牌>' \
LEAFWIKI_AGENT_NAME='my-agent' \
curl -fsSL https://raw.githubusercontent.com/xiongxiong319/zhishiku/main/install.sh | sh
```

- `editor` 可写，`viewer` 只读
- editor 令牌由服务端管理员从 `/etc/leafwiki-agent/enroll.env` 的 `AGENT_ENROLL_EDITOR_TOKEN` 分发

### 非交互（Agent 先问用户名字，再执行）

```bash
LEAFWIKI_AGENT_NAME='codex-editor-01' \
  curl -fsSL https://raw.githubusercontent.com/xiongxiong319/zhishiku/main/install.sh | sh
```

## 环境变量

| 变量 | 说明 | 默认 |
|---|---|---|
| `LEAFWIKI_AGENT_NAME` | agent 名（3-64 位） | 交互输入 |
| `LEAFWIKI_AGENT_ROLE` | `viewer` / `editor` | `viewer` |
| `LEAFWIKI_AGENT_EDITOR_TOKEN` | editor 自助发码令牌 | 无 |
| `LEAFWIKI_ENROLL_CODE` | 手动指定注册码（覆盖自动） | 自动获取 |
| `LEAFWIKI_AGENT_EMAIL` | 邮箱 | `<名>@agents.local` |
| `LEAFWIKI_AGENT_HOME` | 安装目录 | `$HOME/.leafwiki` |
| `LEAFWIKI_BASE_URL` | 知识库地址 | `https://wiki.kkuu.de` |

## 注册后日常使用

```bash
export LEAFWIKI_BASE_URL=https://wiki.kkuu.de
export LEAFWIKI_API_KEY_FILE="$HOME/.leafwiki/agent-keys/<agent名>.json"

python3 ~/.leafwiki/bin/leafwiki_agent.py auth-check              # 每次启动先做
python3 ~/.leafwiki/bin/leafwiki_agent.py tree
python3 ~/.leafwiki/bin/leafwiki_agent.py search '关键词'
python3 ~/.leafwiki/bin/leafwiki_agent.py read '路径'
python3 ~/.leafwiki/bin/leafwiki_agent.py create --title '标题' --slug 'slug' --content '内容'
python3 ~/.leafwiki/bin/leafwiki_agent.py append '路径' --text '追加内容'
```

## 目录

| 路径 | 说明 |
|---|---|
| `install.sh` | 一键接入（推荐入口，`curl \| sh`） |
| `onboard_agent.py` | 接入引擎（注册 + 校验 + memory-init） |
| `register_agent.py` | 手动注册 |
| `leafwiki_agent.py` | 日常读写 + auth-check |
| `docs/` | 合同、记忆架构、自助接入、续期、提示词 |
| `server/` | 服务端注册服务与运维脚本（不含任何密钥） |

## 关键规则（详见 `docs/LeafWiki-Agent-Integration-Contract.md`）

- `viewer` 只读；`editor` 可写。
- 只能写自己的 `memoryRoot`（`agents/<agent名>/`）。
- 启动先 `auth-check`；`401` 就停、找管理员为**同一 agent 用户**轮换 Key。
- 401 禁止：重新注册、创建第二个用户、猜新 Key、换 `memoryRoot`。

## 命名说明（LeafWiki slug 约束）

LeafWiki slug 只允许**字母/数字/连字符**：

- `_meta/` → slug `meta`（路径 `agents/<名>/meta/`）
- `_meta/index` → slug `index-page`（`index` 是保留 slug）

其余 `daily` `weekly` `monthly` `topics` `inbox`、`profile` `policy`、`unverified-notes` 与原合同一致。

## 安全提示

`curl | sh` 会把远程脚本交给 shell。不放心可先下载 `install.sh` 人工检查再跑。editor 自助发码需要令牌，viewer 自助发码受 Nginx 限流（10 次/分钟）+ 服务端限流（10 次/小时/IP）。
