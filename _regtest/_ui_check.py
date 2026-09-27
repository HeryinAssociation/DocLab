"""把 ui/index.html 里的 <script> 抠出来交给 node --check，并做几条一致性自检。"""
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab")
NODE = r"C:\Users\Zhaoshuochen\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"
HTML = ROOT / "ui" / "index.html"
tmp = Path(r"C:\Users\Zhaoshuochen\AppData\Local\Temp") / "_doclab_ui_check.js"

text = HTML.read_text(encoding="utf-8")
m = re.search(r"<script>(.*)</script>", text, re.S)
if not m:
    print("找不到 <script>")
    sys.exit(1)
js = m.group(1)
tmp.write_text(js, encoding="utf-8")

rc = subprocess.run([NODE, "--check", str(tmp)], capture_output=True, text=True)
print("node --check rc =", rc.returncode)
if rc.stdout.strip():
    print(rc.stdout.strip()[:2000])
if rc.stderr.strip():
    print(rc.stderr.strip()[:2000])

print("\n--- 一致性自检 ---")
checks = [
    ("定义 rowClick", "function rowClick(ev, i)" in js),
    ("定义 setVDepth", "function setVDepth(k)" in js),
    ("定义 setShowDel", "function setShowDel(on)" in js),
    ("定义 renderViewBar", "function renderViewBar()" in js),
    ("定义 loadView", "function loadView()" in js),
    ("boot 调 loadView", "loadView();" in js),
    ("行上挂 onclick", 'onclick="rowClick(event, ${i})"' in js),
    ("勾选框不再自己 onclick", 'onclick="toggleSelAt' not in js),
    ("toggleSelAt 已无残留", "toggleSelAt" not in js),
    ("渲染时按深度过滤", "VDEPTH && (depth[n.key] || 1) > VDEPTH" in js),
    ("藏起来的行不进 VISIBLE", "if (n.deleted ? !SHOW_DEL" in js),
    ("renderTreeList 调 renderViewBar", "renderViewBar();" in js),
    ("vdKb 容器存在", 'id="vdKb"' in text),
    ("showDelCk 容器存在", 'id="showDelCk"' in text),
    ("viewBar 容器存在", 'id="viewBar"' in text),
    ("vdMeta 容器存在", 'id="vdMeta"' in text),
    ("勾选框指针穿透", "pointer-events:none" in text),
]
bad = 0
for name, ok in checks:
    print(("  OK  " if ok else "  ✗   ") + name)
    bad += 0 if ok else 1

# 括号配平（粗查）
for ch_open, ch_close in "{}", "()", "[]":
    print(f"  {ch_open}{ch_close}: {js.count(ch_open)} / {js.count(ch_close)}"
          + ("  不平衡" if js.count(ch_open) != js.count(ch_close) else ""))

print("\n失败项：", bad)
