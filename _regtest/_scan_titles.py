# -*- coding: utf-8 -*-
"""扫描四本书的「残缺编号标题」与「合成节点 vs 真块重叠」，为修订单取数。

输出（落 _regtest/_out/_scan.txt）：
  1) 每个 L1/L2 节点一行；
  2) 纯编号型标题（「第一章」这种只剩编号的）→ 它的下一块原文（很可能就是被拆出去的
     标题后半）；
  3) 同一个 gid 上既有真块又有 toc: 合成节点 —— 就是 numbering_repeat 的成因。
"""
import os
import re
import sys
import json

sys.stdout.reconfigure(encoding="utf-8")
BASE = r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab"
sys.path.insert(0, BASE)
import doclab                                                    # noqa: E402

BOOKS = [
    ("魂系", "魂系历史主义-西方档案学支柱理论发展研究-Pdg2Pic-黄霄羽著.8701935468"),
    ("现代档案", "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"),
    ("丁华东", "档案与社会记忆研究_丁华东.b8a64398d9"),
    ("胡鸿杰", "中国档案学的理念与模式-胡鸿杰著-Hu-Hong-Jie-Zhu-胡鸿杰-1958--胡鸿杰著-.60ddbd4329"),
]

BARE_NUM = re.compile(r"^第\s*[一二三四五六七八九十百零\d]{1,3}\s*[章节部分篇讲编]\s*[.．、:：]?\s*$")
out = []


def walk(ns, acc):
    for n in ns:
        acc.append(n)
        walk(n.get("children") or [], acc)


for tag, sid in BOOKS:
    wd = os.path.join(BASE, "_work", sid)
    o = json.load(open(os.path.join(wd, "outline.json"), encoding="utf-8"))
    print(f"[{tag}] 读块…", flush=True)
    _wd, _shards, blocks = doclab.load_blocks(sid)
    by_gid = {b.gid: b for b in blocks}
    rows = []
    walk(o["tree"], rows)

    out.append("#" * 78)
    out.append(f"{tag}  nodes={o.get('node_total')}")
    out.append("--- L1/L2 ---")
    for n in rows:
        if n["level"] <= 2:
            out.append(f"  {n['nid']:<8} L{n['level']} gid={str(n.get('gid_start')):<6} "
                       f"key={str(n.get('key'))[:52]:<54} {str(n['title'])[:56]}")

    out.append("--- 纯编号型标题（可能被拆成两块）---")
    for n in rows:
        t = str(n["title"]).strip()
        if not BARE_NUM.match(t):
            continue
        g = n.get("gid_start")
        nxt = by_gid.get(int(g) + 1) if g is not None else None
        line = f"  nid={n['nid']:<8} L{n['level']} gid={g} key={n.get('key')} 「{t}」"
        if nxt is not None:
            line += f"\n        下一块 gid={nxt.gid} type={nxt.type} [h]={bool(nxt.is_heading)}: {str(nxt.text)[:70]}"
        out.append(line)

    out.append("--- 同 gid 上真块与 toc: 合成节点重叠 ---")
    by_g: dict = {}
    for n in rows:
        by_g.setdefault(str(n.get("gid_start")), []).append(n)
    for g, ns in sorted(by_g.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
        if len(ns) < 2:
            continue
        out.append(f"  gid={g}")
        for n in ns:
            out.append(f"      nid={n['nid']:<8} L{n['level']} key={str(n.get('key'))[:56]:<58} {str(n['title'])[:56]}")
    out.append("")

open(os.path.join(BASE, "_regtest", "_out", "_scan.txt"), "w",
     encoding="utf-8").write("\n".join(out))
print("written", len(out), "lines")
