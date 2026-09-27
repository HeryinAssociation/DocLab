# -*- coding: utf-8 -*-
"""魂系第二轮修订单：把 gid=991（正文里只剩「“重新发现”」）的节点换成目录原文全名。

编号「第六章 来源原则被」在正文里被 OCR 吃掉了，只剩后半截「“重新发现”」；目录
第 14 页列的是「第六章 来源原则被“重新发现”」。用目录原文补全标题（与 R5 合成节点
同一性质：标题来自书自己的目录，不是我们编的），并删掉那个只有半截名的真块。
"""
import os
import sys
import json

sys.stdout.reconfigure(encoding="utf-8")
BASE = r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab"
HX = "魂系历史主义-西方档案学支柱理论发展研究-Pdg2Pic-黄霄羽著.8701935468"

o = json.load(open(os.path.join(BASE, "_work", HX, "outline.json"), encoding="utf-8"))
rows = []


def walk(ns):
    for n in ns:
        rows.append(n)
        walk(n.get("children") or [])


walk(o["tree"])
n = next(x for x in rows if str(x.get("key")) == "991")
kids = sum(int(c.get("n_chars") or 0) for c in (n.get("children") or []))
snapshot = {"title": n["title"], "level": n["level"], "marker": n.get("marker"),
            "shard": n.get("shard"), "page_idx": n.get("page_idx"),
            "chars": int(n.get("n_chars") or 0),
            "own_chars": int(n.get("n_chars") or 0) - kids,
            "blocks": int(n.get("n_blocks") or 0), "gid": n.get("gid_start")}

plan = {
    "source_id": HX,
    "name": "魂系：第六章标题按书内目录原文补全",
    "note": "正文 gid=991 是 MinerU 标记的标题块，但原文只剩「“重新发现”」——编号"
            "「第六章 来源原则被」被 OCR 吃掉。目录第 14 页是完整形式。删半截真块、"
            "在同一个块上立断点用目录原文作标题；编号在正文里仍然一字不动。",
    "items": [
        {"action": "delete", "key": "991", "snapshot": snapshot,
         "evidence": "正文 gid=991（P1 物理 146 = 目录所记印刷 131）的标题原文只有"
                     "「“重新发现”」，缺编号与前半「第六章 来源原则被」"},
        {"action": "add", "gid": 991, "title": "第六章 来源原则被“重新发现”",
         "level": 2, "offset": 0,
         "evidence": "书内目录第 14 页列「第六章 来源原则被“重新发现”」（印刷 131）；"
                     "该页物理页 146 与目录页码吻合；标题取自目录原文，未改写"},
    ],
}
p = os.path.join(BASE, "_regtest", "_plans", "魂系2.json")
json.dump(plan, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("ok", p, "| 当前 991 title =", n["title"], "level =", n["level"])
