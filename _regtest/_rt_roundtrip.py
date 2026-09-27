# -*- coding: utf-8 -*-
"""真书全链路实测：toc-export → 无改动导入=0 → 改JSON → 演练 → --apply → 生效 → 恢复。

跑完把结果写到 _regtest/_out/_rt_roundtrip.txt（本脚本自己 print，shell 只收 rc）。
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab")
PY = r"C:\Users\Zhaoshuochen\.workbuddy\binaries\python\versions\3.13.12\python.exe"
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from core.config import work_dir  # noqa: E402

SID_HINT = "丁华东"
BACKUP = ROOT / "_regtest" / "_backup_dh_manual_edits.json"
MOD = ROOT / "_regtest" / "_toc_mod.json"


def cli(*args):
    r = subprocess.run([PY, str(ROOT / "doclab.py"), *args],
                       capture_output=True, text=True, encoding="utf-8")
    return r.returncode, r.stdout, r.stderr


def main():
    rc, out, err = cli("sources", "--json")
    srcs = json.loads(out)
    sid = next(s["source_id"] for s in srcs if SID_HINT in s["source_id"])
    print(f"sid = {sid}")
    wd = work_dir(sid)
    me = wd / "manual_edits.json"
    if me.is_file():
        shutil.copy2(me, BACKUP)
        print(f"已备份 manual_edits → {BACKUP.name}")

    # 1) 导出
    rc, out, err = cli("toc-export", sid, "-d", "3")
    print(f"[1] toc-export rc={rc}")
    print("   " + "\n   ".join(l for l in out.splitlines() if l.strip())[:300])
    exp = json.loads((wd / "目录树.json").read_text(encoding="utf-8"))
    print(f"    导出 {exp['node_count']} 条 / 全书 {exp['node_total_all']}；首条={exp['nodes'][0]}")

    # 2) 无改动导入（演练 + 无 --apply 的 0 条）
    rc, out, err = cli("toc-import", sid, "--file", str(wd / "目录树.json"))
    ok2 = rc == 0 and "无改动" in out
    print(f"[2] 无改动导入 rc={rc} → {'✅ 无改动' if ok2 else '❌ ' + out[-400:]}")

    # 3) 改 JSON：删一条叶子、改一条 level、加一条真 gid 的新标题
    nodes = [dict(n) for n in exp["nodes"]]
    del_key = next(n["key"] for n in nodes if n["level"] == 3 and n["own_chars"] > 0
                   and not str(n.get("marker") or "").startswith("front"))
    lvl_key = next(n["key"] for n in nodes if n["level"] == 2 and n["key"] != del_key)
    # 拿一个真实 gid：选被删条目的 gid（它就是正文块，删完再在同块立新标题＝换标题的标准操作）
    del_node = next(n for n in nodes if n["key"] == del_key)
    mod_nodes = []
    for n in nodes:
        if n["key"] == del_key:
            continue                      # 删
        if n["key"] == lvl_key:
            n = {**n, "level": 3}         # 改级
        mod_nodes.append(n)
    mod_nodes.append({"key": "", "gid": del_node["gid"], "title": del_node["title"] + "（核）",
                      "level": 2, "reason": "全链路实测：删旧条换写法（gid 不变）"})
    MOD.write_text(json.dumps({**exp, "note": "全链路实测", "nodes": mod_nodes},
                              ensure_ascii=False, indent=2), encoding="utf-8")

    # 4) 演练
    rc, out, err = cli("toc-import", sid, "--file", str(MOD))
    print(f"[4] 演练 rc={rc}")
    for l in out.splitlines():
        if l.strip():
            print("    " + l)

    # 5) 落盘
    rc, out, err = cli("toc-import", sid, "--file", str(MOD), "--apply")
    print(f"[5] --apply rc={rc}")
    for l in out.splitlines():
        if l.strip():
            print("    " + l)
    oc = json.loads((wd / "outline.json").read_text(encoding="utf-8"))
    print(f"    重算后 node_total={oc['node_total']}")
    flat = []

    def w(ns):
        for n in ns:
            flat.append(n)
            w(n.get("children") or [])
    w(oc["tree"])
    by_key = {n.get("key"): n for n in flat}
    t_del = del_key not in by_key
    t_lvl = by_key.get(lvl_key, {}).get("level") == 3
    t_add = any(n.get("flags") and "manual" in n.get("flags")
                and n.get("title", "").endswith("（核）") for n in flat)
    print(f"    删生效={t_del}　改级生效={t_lvl}　新增生效={t_add}")

    # 6) 恢复
    if BACKUP.is_file():
        shutil.copy2(BACKUP, me)
        rc, out, err = cli("outline", sid)
        oc2 = json.loads((wd / "outline.json").read_text(encoding="utf-8"))
        print(f"[6] 恢复备份并重算：node_total={oc2['node_total']}（应与改前一致）")
    print("RESULT", "PASS" if (ok2 and t_del and t_lvl and t_add) else "CHECK")


if __name__ == "__main__":
    main()
