#!/usr/bin/env bash
# aite 一键启动（Linux）：建 venv（或 AITE_NO_VENV=1 直用当前环境）-> 装依赖 -> 起服务
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
#   AITE_FORCE_INSTALL=1               强制重装依赖（改了 pyproject.toml 后用；仅 .venv 模式）
#   AITE_SKIP_INSTALL=1                跳过依赖安装直接启动
#   PYTHON=python3                     解释器：.venv 模式用它建 venv；AITE_NO_VENV=1 时直接用它装依赖/起服务
#   AITE_NO_VENV=1                     不建 .venv，直接用 $PYTHON（如已激活的 conda env）装依赖并启动；
#                                     外部环境可能被改动，此模式每次启动都核对依赖（AITE_SKIP_INSTALL=1 可跳过）
#
# 数据落点跟随启动目录：./aite.db（库）、./logs（日志）、./reports（报告），本脚本已固定 cd 到仓库根。
set -euo pipefail

cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
VENV=".venv"
HOST="${AITE_HOST:-0.0.0.0}"
PORT="${AITE_PORT:-8300}"

if [ "${AITE_NO_VENV:-0}" = "1" ]; then
    # 直用当前解释器（conda env 等），不建 .venv
    RUN_PY="$PY"
else
    if [ ! -x "$VENV/bin/python" ]; then
        echo "[aite] 创建虚拟环境 $VENV ..."
        "$PY" -m venv "$VENV"
    fi
    RUN_PY="$VENV/bin/python"
fi

if [ "${AITE_SKIP_INSTALL:-0}" != "1" ] && { [ "${AITE_NO_VENV:-0}" = "1" ] || [ ! -f "$VENV/.installed" ] || [ "${AITE_FORCE_INSTALL:-0}" = "1" ]; }; then
    echo "[aite] 安装依赖（extras: ${AITE_EXTRAS:-无}）..."
    "$RUN_PY" -m pip install --upgrade pip
    # shellcheck disable=SC2086
    "$RUN_PY" -m pip install -e ".${AITE_EXTRAS:+[$AITE_EXTRAS]}"
    if [ "${AITE_NO_VENV:-0}" != "1" ]; then
        touch "$VENV/.installed"
    fi
fi

ARGS=()
[ -n "${AITE_DB:-}" ] && ARGS+=(--db "$AITE_DB")
[ -n "${AITE_TOKEN:-}" ] && ARGS+=(--token "$AITE_TOKEN")

echo "[aite] 启动 http://$HOST:$PORT （Ctrl+C 停止）"
exec "$RUN_PY" -m aite.cli.main serve \
    --host "$HOST" \
    --port "$PORT" \
    ${ARGS[@]+"${ARGS[@]}"} \
    "$@"
