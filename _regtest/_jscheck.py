"""把 ui/index.html 里的 <script> 抽出来跑 node --check（语法级体检）。

为什么不直接把 html 丢给 node：node 只认 js。抽出来既能做语法检查，也能给
「纯函数抽取 + 假 DOM」那类探针当输入。只做语法，不执行。
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = Path(r"C:\Users\Zhaoshuochen\.workbuddy\binaries\node\versions\22.22.2-3\node.exe")


def main() -> int:
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    blocks = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
    if not blocks:
        print("index.html 里没找到 <script>")
        return 1
    tmp = ROOT / "_regtest" / "_ui_script.js"
    tmp.write_text("\n;\n".join(blocks), encoding="utf-8")
    r = subprocess.run([str(NODE), "--check", str(tmp)],
                       capture_output=True, text=True, encoding="utf-8")
    print(f"抽出 {len(blocks)} 段 script → {tmp.name}（{len(tmp.read_text(encoding='utf-8'))} 字符）")
    if r.returncode == 0:
        print("✅ JS 语法通过")
        return 0
    print("❌ JS 语法错误：")
    print(r.stdout or "", r.stderr or "")
    return 1


if __name__ == "__main__":
    sys.exit(main())
