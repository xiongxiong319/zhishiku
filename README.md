# LeafWiki Agent 接入

中心化知识库 `https://wiki.kkuu.de`（LeafWiki v0.14.2）的 **Agent 自助接入**方案。

Agent 只需一个**一次性注册码** + 唯一 **agent 名**，用一键脚本即可完成接入，无需 SSH、无需提前在服务端登记。

## 一键接入（Agent 侧）

```bash
python3 onboard_agent.py --code 'lw_enroll_xxx' --agent-name 'my-agent-01'
```

脚本自动完成四步：

1. **注册**：一次性注册码 → 独立用户 + 独立 API Key + `memoryRoot`
2. **写凭据**：`~/.leafwiki/agent-keys/<agent名>.json`（权限 600，不打印 Key）
3. **auth-check**：校验 Key 是否可用
4. **memory-init**（editor）：在 `memoryRoot` 下建目录骨架

要求：仅 **Python 3 标准库**，无需 `pip install` 任何依赖。

可选参数：

```bash
python3 onboard_agent.py --code '...' --agent-name '...' \
  --email 'agent@example.com' \
  --enroll-url 'https://wiki.kkuu.de/agent-enroll/register' \
  --output-dir "$HOME/.leafwiki/agent-keys"
```

## 注册后日常使用

```bash
export LEAFWIKI_BASE_URL=https://wiki.kkuu.de
export LEAFWIKI_API_KEY_FILE="$HOME/.leafwiki/agent-keys/<agent名>.json"

python3 leafwiki_agent.py auth-check              # 每次启动先做
python3 leafwiki_agent.py tree                     # 页面树
python3 leafwiki_agent.py search '关键词'           # 搜索
python3 leafwiki_agent.py read '路径'              # 读
python3 leafwiki_agent.py create --title '标题' --slug 'slug' --content '内容'   # 写
python3 leafwiki_agent.py append '路径' --text '追加内容'                          # 追加
```

## 目录

| 路径 | 说明 |
|---|---|
| `onboard_agent.py` | 一键接入（推荐入口） |
| `register_agent.py` | 手动注册（onboard 的底层步骤） |
| `leafwiki_agent.py` | 日常读写 + auth-check |
| `docs/` | 合同、记忆架构、自助接入、续期、提示词 |
| `server/` | 服务端注册服务与运维脚本（不含任何密钥） |

## 关键规则（详见 `docs/LeafWiki-Agent-Integration-Contract.md`）

- `viewer` 只读；`editor` 可写（要写记忆的 Agent 用 **editor**）。
- 只能写自己的 `memoryRoot`（`agents/<agent名>/`）。
- 启动先 `auth-check`；收到 `401` 就停、找管理员为**同一 agent 用户**轮换 Key。
- 401 时禁止：重新注册、创建第二个用户、猜新 Key、换 `memoryRoot`。

## 命名说明（LeafWiki slug 约束）

LeafWiki 的 slug 只允许**字母/数字/连字符**，因此：

- 合同里的 `_meta/` → 实际 slug `meta`（路径 `agents/<名>/meta/`）
- 合同里的 `_meta/index` → 实际 slug `index-page`（`index` 是 LeafWiki 保留 slug）

其余目录 `daily` `weekly` `monthly` `topics` `inbox`、页面 `profile` `policy`、`unverified-notes` 与原合同一致。

## 接入方式

默认走公网精确路径 `https://wiki.kkuu.de/agent-enroll/register`（Nginx 反代 + 限流）。
