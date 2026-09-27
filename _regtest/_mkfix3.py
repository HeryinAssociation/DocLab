# -*- coding: utf-8 -*-
"""胡鸿杰补充修订单：第四章标题按目录原文补全（正文 OCR 掉了「的」）。"""
import os
import sys
import json

sys.stdout.reconfigure(encoding="utf-8")
BASE = r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab"
HH = "中国档案学的理念与模式-胡鸿杰著-Hu-Hong-Jie-Zhu-胡鸿杰-1958--胡鸿杰著-.60ddbd4329"

o = json.load(open(os.path.join(BASE, "_work", HH, "outline.json"), encoding="utf-8"))
rows = []


def walk(ns):
    for n in ns:
        rows.append(n)
        walk(n.get("children") or [])


walk(o["tree"])
n = next(x for x in rows if str(x.get("key")) == "add:883")
kids = sum(int(c.get("n_chars") or 0) for c in (n.get("children") or []))
snapshot = {"title": n["title"], "level": n["level"], "marker": n.get("marker"),
            "shard": n.get("shard"), "page_idx": n.get("page_idx"),
            "chars": int(n.get("n_chars") or 0),
            "own_chars": int(n.get("n_chars") or 0) - kids,
            "blocks": int(n.get("n_blocks") or 0), "gid": n.get("gid_start")}

plan = {
    "source_id": HH,
    "name": "胡鸿杰：第四章标题按目录原文补全",
    "note": "正文 gid=883 的标题原文是「档案学学者构成」（OCR 掉了「的」），目录第 13 页"
            "作「第四章 档案学的学者构成」。前一轮已在 gid=883 立过断点，这里把标题"
            "换成目录原文，避免同一条两写法。",
    "items": [
        {"action": "delete", "key": "add:883", "snapshot": snapshot,
         "evidence": "上一轮在 gid=883 新增的标题写作「档案学学者构成」，与书内目录"
                     "「第四章 档案学的学者构成」差一个「的」，目录交叉校验对不上"},
        {"action": "add", "gid": 883, "title": "第四章 档案学的学者构成",
         "level": 1, "offset": 0,
         "evidence": "书内目录第 13 页列「第四章 档案学的学者构成」（印刷 113，目录与"
                     "正文位置一致）；正文 gid=883 是 MinerU 标记的标题块，原文作"
                     "「档案学学者构成」；标题取目录原文，未改写"},
    ],
}
p = os.path.join(BASE, "_regtest", "_plans", "胡鸿杰2.json")
json.dump(plan, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("ok", p, "| add:883 →", n["title"])
