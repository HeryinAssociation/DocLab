# -*- coding: utf-8 -*-
"""现代档案收尾：撤销 add:2508（空壳），改为把实块节点 2508 定级到 L2。
策略：toc-export -d 3 → 改 JSON（删 add:2508 条 / 2508 条 level 3→2）→ 演练 → apply → 复导出 → audit → export+verify。"""
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


# 1) 深度 3 导出
run(["toc-export", SID, "-d", "3"], r"_regtest/_out/_xd3_exp.txt")
exp = json.loads((WD / "目录树.json").read_text(encoding="utf-8"))
nodes = exp["nodes"]
print(f"导出 {len(nodes)} 条（≤L3）")

# 2) 找到两处
add_i = next(i for i, n in enumerate(nodes) if n.get("key") == "add:2508")
real_i = next(i for i, n in enumerate(nodes) if n.get("key") == "2508")
print(f"  add:2508 在第 {add_i} 位（L{nodes[add_i]['level']}，own={nodes[add_i].get('own_chars')}）")
print(f"  2508     在第 {real_i} 位（L{nodes[real_i]['level']}，own={nodes[real_i].get('own_chars')}，"
      f"flags={nodes[real_i].get('flags')}）")

# 3) 改：删掉 add 条；把实块条定级到 2
new_nodes = [n for n in nodes if n.get("key") != "add:2508"]
r_i = next(i for i, n in enumerate(new_nodes) if n.get("key") == "2508")
new_nodes[r_i] = dict(new_nodes[r_i])
new_nodes[r_i]["level"] = 2
new_nodes[r_i]["note"] = ("印刷目录「档案与文化……(402)」；正文 gid=2508 [h] 章标题，"
                          "原被自动定为 L3（挂在 11.3 档案与公共关系下），按目录归位到第九编下 L2。"
                          "只改级、不新增节点 —— 前一版用「删旧增新」留下了 0 字空壳，此处改正")

new = dict(exp)
new["nodes"] = new_nodes
new["depth_limit"] = 3
new["note"] = "L2 语义核对收尾：把「档案与文化」由删旧增新改为直接定级 L2（撤销 add:2508 空壳）"
mod = WD / "目录树_改2.json"
mod.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"改后 JSON: {mod}（{len(new_nodes)} 条）")

# 4) 演练
r = run(["toc-import", SID, "--file", str(mod)], r"_regtest/_out/_xd3_dry.txt")
print(r.stdout[:1500])

# 5) 落盘
r = run(["toc-import", SID, "--file", str(mod), "--apply"], r"_regtest/_out/_xd3_apply.txt")
print(r.stdout[:1500])

# 6) 复导出 + audit + export + verify
run(["toc-export", SID, "-d", "2"], r"_regtest/_out/_xd3_reexp.txt")
run(["audit", SID], r"_regtest/_out/_xd3_audit.txt")
run(["export", SID, "-d", "2"], r"_regtest/_out/_xd3_export.txt")
r = run(["verify", SID, "-d", "2"], r"_regtest/_out/_xd3_verify.txt")
print(r.stdout[-600:])
