# -*- coding: utf-8 -*-
"""现代档案：补 key=496 截断章标题（gid496 + gid497 逐字拼接）。
toc-export -d 2 → 删 496 条 + 同 gid 新增全题 → 演练 → apply → 复导出 → audit → export+verify。"""
import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

PY = r"C:\Users\Zhaoshuochen\.workbuddy\binaries\python\versions\3.13.12\python.exe"
ROOT = r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab"
DL = ROOT + r"\doclab.py"
SID = "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"
WD = Path(ROOT) / "_work" / SID


def run(args, log):
    r = subprocess.run([PY, DL, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT)
    Path(ROOT, log).write_text(r.stdout + r.stderr, encoding="utf-8")
    print(f"[{args[0]}] rc={r.returncode} → {log}")
    return r


# 0) 先确认两块原文与 key=497 的命运
oc = json.loads((WD / "outline.json").read_text(encoding="utf-8"))
flat = []
def walk(ns):
    for n in ns:
        flat.append(n)
        walk(n.get("children") or [])
walk(oc["tree"])
for want in ("496", "497"):
    hit = [n for n in flat if str(n.get("key")) == want]
    for n in hit:
        print(f"  树里 key={want}: L{n.get('level')} nid={n.get('nid')} title={n.get('title')!r}")
    if not hit:
        print(f"  树里 key={want}: 无节点")

# 1) 导出
run(["toc-export", SID, "-d", "2"], r"_regtest/_out/_xd4_exp.txt")
exp = json.loads((WD / "目录树.json").read_text(encoding="utf-8"))
nodes = exp["nodes"]
i496 = next(i for i, n in enumerate(nodes) if n.get("key") == "496")
print(f"\n导出第 {i496} 条: {json.dumps(nodes[i496], ensure_ascii=False)}")

# 2) 改：删 496 条；在 495（第二编）之后插入全题新增条
FULL = "作为档案的历史手稿——几种定义及其应用"
new_nodes = [n for n in nodes if n.get("key") != "496"]
pos = next(i for i, n in enumerate(new_nodes) if n.get("key") == "495")   # 第二编
new_nodes.insert(pos + 1, {
    "key": "", "gid": 496, "title": FULL, "level": 2,
    "note": "章标题被 MinerU 拆成两块：gid=496「作为档案的历史手稿」+ gid=497「——几种定义及其应用」。"
            "印刷目录作「作为档案的历史手稿——几种定义及其应用…(73)」。逐字拼接两块原文，不添不删字",
})
new = dict(exp)
new["nodes"] = new_nodes
new["note"] = "L2 语义核对：补 496 章标题截断（与 gid=497 逐字拼接）"
mod = WD / "目录树_改3.json"
mod.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"改后 JSON（{len(new_nodes)} 条）")

# 3) 演练 + 落盘
r = run(["toc-import", SID, "--file", str(mod)], r"_regtest/_out/_xd4_dry.txt")
print(r.stdout[:900])
r = run(["toc-import", SID, "--file", str(mod), "--apply"], r"_regtest/_out/_xd4_apply.txt")
print(r.stdout[:900])

# 4) 复导出 + audit + export + verify
run(["toc-export", SID, "-d", "2"], r"_regtest/_out/_xd4_reexp.txt")
run(["audit", SID], r"_regtest/_out/_xd4_audit.txt")
run(["export", SID, "-d", "2"], r"_regtest/_out/_xd4_export.txt")
r = run(["verify", SID, "-d", "2"], r"_regtest/_out/_xd4_verify.txt")
print(r.stdout[-500:])
