#!/usr/bin/env bash
# aite 一键启动（Linux）：建 venv -> 装依赖 -> 起服务
#
# 用法：
#   bash start.sh                    # 首次自动建 .venv 并安装依赖，然后起服务（前台）
#   ./start.sh --reload              # 其余参数原样透传给 aite serve
#
# 可用环境变量（都有默认值，可不设）：
#   AITE_HOST=0.0.0.0                  监听地址
#   AITE_PORT=8300                     监听端口
#   AITE_DB=                           存储覆盖，SQLAlchemy URL（如 sqlite+aiosqlite:////path/x.db）
#   AITE_TOKEN=                        接口 bearer token（留空 = 不设鉴权）
#   AITE_EXTRAS=functional,bench       pip 安装的 extras（逗号分隔；默认只装核心依赖）
#   AITE_FORCE_INSTALL=1               强制重装依赖（改了 pyproject.toml 后用）
#   AITE_SKIP_INSTALL=1                跳过依赖安装直接启动
#   PYTHON=python3                     建 venv 用的解释器
#
# 数据落点跟随启动目录：./aite.db（库）、./logs（日志）、./reports（报告），本脚本已固定 cd 到仓库根。
set -euo pipefail

cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
VENV=".venv"
HOST="${AITE_HOST:-0.0.0.0}"
PORT="${AITE_PORT:-8300}"

if [ ! -x "$VENV/bin/python" ]; then
    echo "[aite] 创建虚拟环境 $VENV ..."
    "$PY" -m venv "$VENV"
fi

if [ "${AITE_SKIP_INSTALL:-0}" != "1" ] && { [ ! -f "$VENV/.installed" ] || [ "${AITE_FORCE_INSTALL:-0}" = "1" ]; }; then
    echo "[aite] 安装依赖（extras: ${AITE_EXTRAS:-无}）..."
    "$VENV/bin/pip" install --upgrade pip
    # shellcheck disable=SC2086
    "$VENV/bin/pip" install -e ".${AITE_EXTRAS:+[$AITE_EXTRAS]}"
    touch "$VENV/.installed"
fi

ARGS=()
[ -n "${AITE_DB:-}" ] && ARGS+=(--db "$AITE_DB")
[ -n "${AITE_TOKEN:-}" ] && ARGS+=(--token "$AITE_TOKEN")

echo "[aite] 启动 http://$HOST:$PORT （Ctrl+C 停止）"
exec "$VENV/bin/python" -m aite.cli.main serve \
    --host "$HOST" \
    --port "$PORT" \
    ${ARGS[@]+"${ARGS[@]}"} \
    "$@"
