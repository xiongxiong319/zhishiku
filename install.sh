#!/bin/sh
# LeafWiki Agent 一键接入（安装 + 注册 + auth-check + memory-init）
#
# 用法（二选一）：
#   curl -fsSL https://raw.githubusercontent.com/xiongxiong319/zhishiku/main/install.sh | sh -s -- '<注册码>' '<agent名>'
#   LEAFWIKI_ENROLL_CODE='码' LEAFWIKI_AGENT_NAME='名' curl -fsSL https://raw.githubusercontent.com/xiongxiong319/zhishiku/main/install.sh | sh
#
# 可选环境变量：
#   LEAFWIKI_AGENT_EMAIL  邮箱（默认 <agent名>@agents.local）
#   LEAFWIKI_AGENT_HOME   安装目录（默认 $HOME/.leafwiki）
#   LEAFWIKI_BASE_URL     知识库地址（默认 https://wiki.kkuu.de）

set -e

CODE="${1:-${LEAFWIKI_ENROLL_CODE:-}}"
NAME="${2:-${LEAFWIKI_AGENT_NAME:-}}"
EMAIL="${LEAFWIKI_AGENT_EMAIL:-}"

if [ -z "$CODE" ] || [ -z "$NAME" ]; then
    echo "用法:" >&2
    echo "  curl -fsSL https://raw.githubusercontent.com/xiongxiong319/zhishiku/main/install.sh | sh -s -- '<注册码>' '<agent名>'" >&2
    echo "或:" >&2
    echo "  LEAFWIKI_ENROLL_CODE='码' LEAFWIKI_AGENT_NAME='名' curl -fsSL https://raw.githubusercontent.com/xiongxiong319/zhishiku/main/install.sh | sh" >&2
    exit 1
fi

command -v python3 >/dev/null 2>&1 || { echo "错误: 未找到 python3" >&2; exit 1; }

RAW="https://raw.githubusercontent.com/xiongxiong319/zhishiku/main"
HOME_DIR="${LEAFWIKI_AGENT_HOME:-$HOME/.leafwiki}"
BIN="$HOME_DIR/bin"
KEYS="$HOME_DIR/agent-keys"
mkdir -p "$BIN" "$KEYS"

echo "[*] 下载客户端脚本 ..."
curl -fsSL "$RAW/onboard_agent.py" -o "$BIN/onboard_agent.py"
curl -fsSL "$RAW/leafwiki_agent.py" -o "$BIN/leafwiki_agent.py"
chmod 700 "$BIN/onboard_agent.py" "$BIN/leafwiki_agent.py"

echo "[*] 一键接入 agent=$NAME ..."
set -- "$BIN/onboard_agent.py" --code "$CODE" --agent-name "$NAME" --output-dir "$KEYS"
[ -n "$EMAIL" ] && set -- "$@" --email "$EMAIL"
python3 "$@"

echo
echo "[+] 完成。后续使用："
echo "    export LEAFWIKI_BASE_URL=https://wiki.kkuu.de"
echo "    export LEAFWIKI_API_KEY_FILE=\"$KEYS/$NAME.json\""
echo "    python3 \"$BIN/leafwiki_agent.py\" auth-check"
