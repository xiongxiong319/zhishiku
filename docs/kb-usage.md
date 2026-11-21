# kb.py — Nowen Note 知识库工具（多 agent 隔离）

给 AI agent 读写 Nowen Note 知识库的命令行工具，支持多 agent 目录隔离。

## 一键安装

```bash
curl -fsSL https://raw.githubusercontent.com/xiongxiong319/zhishiku/main/kb.py -o /usr/local/bin/kb && chmod +x /usr/local/bin/kb
```

## 配置（环境变量，均可省略）

| 变量 | 默认 | 说明 |
|---|---|---|
| KB_BASE | https://wiki.kkuu.de | 知识库 API 地址 |
| KB_ADMIN | admin | 登录账号 |
| KB_PASSWORD | 无 | 登录密码（环境变量，或写 ~/.kb_password 文件） |
| KB_AGENT | harness | 本 agent 名（决定写哪个目录，隔离关键） |

> KB_AGENT 也可写 ~/.kb_agent 文件（内容即 agent 名）。

## 命令

| 命令 | 作用 |
|---|---|
| kb context | 开工：注入人格（SOUL+牛牛）+规范+本 agent 近期记忆 |
| kb search "关键词" | 全局全文搜索 |
| kb remember --ws <工作区> --text "..." --type decision | 写当日记忆（只写本 agent 目录） |
| kb wrap-up | 收尾检查清单 |

## 目录隔离

每个 agent 用不同的 KB_AGENT，写到自己顶层目录（如 harness/、claude/ 下各自的 01-每日记录/<工作区>/），不会串目录。

## 人格与规范

知识库 00-全局/ 下的 SOUL、AGENTS_角色设定（牛牛）、AGENTS 通用守则 是所有人格与规范的唯一来源。
