#!/bin/sh
# LeafWiki Agent 一键接入（零参数版）
#
# 用法：
#   curl -fsSL https://raw.githubusercontent.com/xiongxiong319/zhishiku/main/install.sh | sh
#     -> 自动获取 viewer 注册码，交互式输入 agent 名（有终端时）
#
#   curl -fsSL https://raw.githubusercontent.com/xiongxiong319/zhishiku/main/install.sh | sh -s -- '' 'agent名'
#   LEAFWIKI_AGENT_NAME='agent名' curl -fsSL .../install.sh | sh
#     -> 非交互（Agent 先问你要名字，再带名字执行）
#
# 可选环境变量：
#   LEAFWIKI_AGENT_ROLE          viewer(默认,只读) / editor(可写)
#   LEAFWIKI_AGENT_EDITOR_TOKEN  editor 自助发码令牌（写记忆时用）
#   LEAFWIKI_ENROLL_CODE         手动指定注册码（覆盖自动获取）
#   LEAFWIKI_AGENT_NAME          agent 名
#   LEAFWIKI_AGENT_EMAIL         邮箱
#   LEAFWIKI_AGENT_HOME          安装目录（默认 $HOME/.leafwiki）
#   LEAFWIKI_BASE_URL            知识库地址（默认 https://wiki.kkuu.de）

set -e

BASE_URL="${LEAFWIKI_BASE_URL:-https://wiki.kkuu.de}"
CODE="${1:-${LEAFWIKI_ENROLL_CODE:-}}"
NAME="${2:-${LEAFWIKI_AGENT_NAME:-}}"
ROLE="${LEAFWIKI_AGENT_ROLE:-viewer}"
EDITOR_TOKEN="${LEAFWIKI_AGENT_EDITOR_TOKEN:-}"
EMAIL="${LEAFWIKI_AGENT_EMAIL:-}"

command -v python3 >/dev/null 2>&1 || { echo "错误: 需要 python3" >&2; exit 1; }
command -v curl   >/dev/null 2>&1 || { echo "错误: 需要 curl" >&2; exit 1; }

# 1) 注册码：未提供则自动获取
if [ -z "$CODE" ]; then
    echo "[*] 自动获取注册码 (role=$ROLE) ..."
    case "$ROLE" in
        editor)
            if [ -z "$EDITOR_TOKEN" ]; then
                echo "错误: editor 需要写权限令牌，请设置 LEAFWIKI_AGENT_EDITOR_TOKEN（或手动 LEAFWIKI_ENROLL_CODE）" >&2
                exit 1
            fi
            CODE=$(curl -fsSL -H "X-Editor-Token: $EDITOR_TOKEN" "$BASE_URL/agent-enroll/issue?role=editor" \
                   | python3 -c "import sys,json;print(json.load(sys.stdin)['code'])")
            ;;
        *)
            ROLE=viewer
            CODE=$(curl -fsSL "$BASE_URL/agent-enroll/issue" \
                   | python3 -c "import sys,json;print(json.load(sys.stdin)['code'])")
            ;;
    esac
    [ -n "$CODE" ] || { echo "错误: 获取注册码失败" >&2; exit 1; }
fi

# 2) agent 名：未提供则交互输入（有终端时从 /dev/tty 读，绕过 stdin 管道）
if [ -z "$NAME" ]; then
    if [ -t 0 ] || [ -c /dev/tty ]; then
        printf '请输入 agent 名 (3-64 位，字母/数字/._-): '
        read -r NAME < /dev/tty 2>/dev/null || true
    fi
    if [ -z "$NAME" ]; then
        echo "错误: 缺少 agent 名。" >&2
        echo "      非交互方式请先确定名字再执行，例如：" >&2
        echo "      LEAFWIKI_AGENT_NAME='my-agent' curl -fsSL $BASE_URL/../install.sh | sh" >&2
        exit 1
    fi
fi

# 3) 下载客户端脚本
RAW="https://raw.githubusercontent.com/xiongxiong319/zhishiku/main"
HOME_DIR="${LEAFWIKI_AGENT_HOME:-$HOME/.leafwiki}"
BIN="$HOME_DIR/bin"
KEYS="$HOME_DIR/agent-keys"
mkdir -p "$BIN" "$KEYS"
echo "[*] 下载客户端脚本 ..."
curl -fsSL "$RAW/onboard_agent.py" -o "$BIN/onboard_agent.py"
curl -fsSL "$RAW/leafwiki_agent.py" -o "$BIN/leafwiki_agent.py"
chmod 700 "$BIN/onboard_agent.py" "$BIN/leafwiki_agent.py"

# 4) 一键接入
echo "[*] 一键接入 agent=$NAME (role=$ROLE) ..."
set -- "$BIN/onboard_agent.py" --code "$CODE" --agent-name "$NAME" --output-dir "$KEYS"
[ -n "$EMAIL" ] && set -- "$@" --email "$EMAIL"
python3 "$@"

echo
echo "[+] 完成。后续使用："
echo "    export LEAFWIKI_BASE_URL=$BASE_URL"
echo "    export LEAFWIKI_API_KEY_FILE=\"$KEYS/$NAME.json\""
echo "    python3 \"$BIN/leafwiki_agent.py\" auth-check"
