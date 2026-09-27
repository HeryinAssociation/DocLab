# -*- coding: utf-8 -*-
"""现代档案：改目录 JSON（4 个动作）→ toc-import 演练 → --apply → 复导出 → audit → export+verify。"""
import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

PY = r"C:\Users\Zhaoshuochen\.workbuddy\binaries\python\versions\3.13.12\python.exe"
DL = r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab\doclab.py"
SID = "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"
WD = Path(r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab\_work") / SID

def run(args, log):
    r = subprocess.run([PY, DL, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(DL.rsplit("\\", 1)[0]))
    Path(log).write_text(r.stdout + r.stderr, encoding="utf-8")
    print(f"[{args[0]}] rc={r.returncode} → {log}")
    return r

# 1) 导出
run(["toc-export", SID, "-d", "2"], r"_regtest/_out/_xd2_exp.txt")
exp = json.loads((WD / "目录树.json").read_text(encoding="utf-8"))
nodes = exp["nodes"]

# 2) 改 JSON
for n in nodes:
    # ① 前言：在「前置」之后插入 L1 节点
    pass
front = nodes[0]
idx = nodes.index(front)
new_nodes = nodes[:idx + 1] + [
    {"key": "", "gid": 42, "title": "前 言", "level": 1,
     "note": "印刷目录首条「前言……(1)」；正文 gid=42 [h]「前 言」被 R2 前置区吞进兜底节点，立断点恢复"},
] + nodes[idx + 1:]

# ② 第九编：删拆半条 + 增全题（正文两行合为一行标题，逐字取自 gid=2361/2362 原文）
new_nodes = [n for n in new_nodes if n.get("key") != "2361"]
for i, n in enumerate(new_nodes):
    if n.get("key") == "1098":  # 第五编前插？不：位置按文档序，插在 2052（第八编）之后
        pass
# 找第八编（2052）的位置，在其后插第九编新节点
pos = next(i for i, n in enumerate(new_nodes) if n.get("key") == "2052")
new_nodes.insert(pos + 1, {
    "key": "", "gid": 2361, "title": "第九编 展览、教育服务和公共关系", "level": 1,
    "note": "正文 gid=2361「第九编 展览、教育服务」+ gid=2362「和公共关系」两行被拆，"
            "合并为印刷目录口径的整标题；逐字取自正文两块原文、以空格连接（不添不删字）",
})

# ③ 档案与文化：在 10.2（2421）之后插 L2
pos2 = next(i for i, n in enumerate(new_nodes if isinstance(new_nodes, list) else new_nodes)
            if n.get("key") == "2421")
new_nodes.insert(pos2 + 1, {
    "key": "", "gid": 2508, "title": "档案与文化", "level": 2,
    "note": "印刷目录「档案与文化……(402)」；正文 gid=2508 [h] 章标题（后随作者行 gid=2509），"
            "误为 10.3 的 L3 子节；按目录归位为第九编下 L2",
})

new = {
    "source_id": SID,
    "doc_title": exp.get("doc_title", ""),
    "depth_limit": 2,
    "node_total_all": exp["node_total_all"],
    "note": "L2 语义核对：① 前言（目录 p1，正文 [h] gid=42）立 L1；"
            "② 第九编标题两行合一（gid=2361+2362）；③ 「档案与文化」（目录 p402，正文 [h] gid=2508）"
            "从 10.3 的 L3 升为第九编下 L2；并删被拆半的旧 2361 节点",
    "nodes": new_nodes,
}
mod = WD / "目录树_改.json"
mod.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"改后 JSON: {mod}（{len(new_nodes)} 条）")

# 3) 演练
r = run(["toc-import", SID, "--file", str(mod)], r"_regtest/_out/_xd2_dry.txt")
print(r.stdout[:1200])

# 4) 落盘
r = run(["toc-import", SID, "--file", str(mod), "--apply"], r"_regtest/_out/_xd2_apply.txt")
print(r.stdout[:1200])

# 5) 复导出 + audit + export + verify
run(["toc-export", SID, "-d", "2"], r"_regtest/_out/_xd2_reexp.txt")
run(["audit", SID], r"_regtest/_out/_xd2_audit.txt")
run(["export", SID, "-d", "2"], r"_regtest/_out/_xd2_export.txt")
r = run(["verify", SID, "-d", "2"], r"_regtest/_out/_xd2_verify.txt")
print(r.stdout[-800:])

# 6) 终态摘要
exp2 = json.loads((WD / "目录树.json").read_text(encoding="utf-8"))
print(f"\n复导出: {exp2['node_count']} 条（≤L2）/ 全书 {exp2['node_total_all']}")
for n in exp2["nodes"]:
    if n.get("key") in ("add:42", "add:2361", "add:2508") or n.get("gid") in (42, 2361, 2508):
        print(f"  L{n['level']} {n.get('nid')} {n['title']!r} key={n['key']}")

aud = json.loads((WD / "目录校核.json").read_text(encoding="utf-8"))
sev = {}
for f in aud.get("findings", []):
    sev[f.get("severity")] = sev.get(f.get("severity"), 0) + 1
print(f"audit: verdict={aud.get('verdict')} 按严重度={sev}")
