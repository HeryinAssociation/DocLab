#!/bin/bash
# doclab · macOS 启动器
# 双击运行；首次会创建 .venv 并安装 pypdf / PyMuPDF，之后直接启动。
# 关闭窗口或按 Ctrl-C 即停止服务。

set -u
cd "$(dirname "$0")" || {
  echo "❌ 无法进入 doclab 目录"
  read -r -p "按回车退出…" _ || true
  exit 1
}

pause() {
  echo
  read -r -p "按回车退出…" _ || true
}

fail() {
  echo
  echo "❌ $*"
  pause
  exit 1
}

echo "doclab · macOS 启动器"
echo "目录：$PWD"
echo

PY=""
for cand in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 python; do
  if command -v "$cand" >/dev/null 2>&1 && \
     "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    PY="$cand"
    break
  fi
done
[ -n "$PY" ] || fail "未找到 Python 3.9+。请先安装 Python 3（https://www.python.org/downloads/macos/），再双击本文件。"

echo "Python：$PY"
if [ ! -x ".venv/bin/python" ]; then
  echo "首次运行：创建虚拟环境 .venv …"
  "$PY" -m venv .venv || fail "创建 .venv 失败。请确认 Python 安装包含 venv/ensurepip。"
fi

VENV_PY=".venv/bin/python"
"$VENV_PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' \
  || fail ".venv 里的 Python 低于 3.9，请删除 .venv 后重试。"

if ! "$VENV_PY" -c 'import pypdf, fitz' >/dev/null 2>&1; then
  echo "安装依赖（pypdf、PyMuPDF）…"
  "$VENV_PY" -m pip install -r requirements.txt --disable-pip-version-check \
    || fail "依赖安装失败。请检查网络后重试：$VENV_PY -m pip install -r requirements.txt"
fi

echo
echo "启动 doclab …"
echo "  URL   http://127.0.0.1:8765/"
echo "  停止  关闭本窗口，或按 Ctrl-C"
echo
exec "$VENV_PY" doclab.py ui --port 8765
