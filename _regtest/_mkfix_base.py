# -*- coding: utf-8 -*-
"""生成基线书（中国数字人文发展报告）的修订单：①P1 偏移纠偏 ②补回被丢弃的章标题。

标题**逐字取自 outline.json 的 dropped_headings**，不手打，杜绝转写走样。
"""
import os
import sys
import json

sys.stdout.reconfigure(encoding="utf-8")
BASE = r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab"
SID = "中国数字人文发展报告"
WD = os.path.join(BASE, "_work", SID)
PLANS = os.path.join(BASE, "_regtest", "_plans")
os.makedirs(PLANS, exist_ok=True)

oc = json.load(open(os.path.join(WD, "outline.json"), encoding="utf-8"))
rep = json.load(open(os.path.join(WD, "目录校核.json"), encoding="utf-8"))

# ① 找到那条被丢弃的章标题（DROPPED-020）
dropped = None
for d in oc.get("dropped_headings") or []:
    if int(d.get("gid", -1)) == 2294:
        dropped = d
        break
assert dropped, "没找到 gid=2294 的丢弃项"
title = dropped["text"].split("\n")[0].strip()
print("补入标题（原文逐字）：", repr(title))

# ② 校核发现 id
f_anchor = next(f["id"] for f in rep["findings"] if f["check"] == "toc_page_offset_shift")
f_drop = next(f["id"] for f in rep["findings"] if f["check"] == "dropped_toc_backed")

plan = {
    "name": "基线：P1 正文区偏移 +2 纠偏 ／ 补回被规则丢弃的章标题",
    "note": ("两条都是校核判定的必处理项，且都已取证到书内目录与正文块，不是猜测：\n"
             "① P1 正文区现有锚点 page_idx=20→printed=10 使 offset=−10，"
             "与自动校准（181 页页码观测、覆盖 0.978）的 −12 矛盾，"
             "并与 P2 起页 188 造成 2 页重叠；改回 8 后 4.2–4.6 五章与书内目录"
             "页号（57/85/116/148/173）逐条对齐、P1/P2 无缝接续。\n"
             "② 第二部分「中国艺术学科数字人文发展报告」的章标题原文带副题、"
             "末尾有问号，被「不像标题」规则丢弃；正文 gid=2294 就是该标题本身"
             "（落 P2 物理页 99＝印刷页 287，与目录一致），按原文补回标题断点。"),
    "based_on_audit_at": rep.get("generated_at") or "",
    "items": [
        {"action": "anchor", "shard": "P1", "page_idx": 20, "printed": 8,
         "evidence": (f"校核 {f_anchor}（整批章节齐刷刷偏 +2 页，5 条：4.2–4.6）。"
                      "P1 正文区自动校准为 offset=−12（181 页观测、覆盖 0.978）；"
                      "现人工锚点 page_idx=20→printed=10 得 −10，与观测矛盾，"
                      "且使 P1 末页落到 189、而 P2 起页是 188（重叠 2 页）。"
                      "书内目录列出 4.2=57 4.3=85 4.4=116 4.5=148 4.6=173，"
                      "按 −12 落点 57/85/116/148/173 逐条吻合。故 printed 应为 8。"),
         "finding": f_anchor},
        {"action": "add", "gid": 2294, "title": title, "level": 2, "offset": 0,
         "evidence": (f"校核 {f_drop}：书内目录第 287 页列出「中国艺术学科数字人文发展报告」，"
                      "但正文标题原文带副题且以问号结尾，被「不像标题（过长或带句末标点）」"
                      f"规则丢弃（gid={dropped.get('gid')}，{dropped.get('shard')} 物理页 "
                      f"{dropped.get('page_idx')}＝印刷页 287，与目录页号一致）。"
                      "按约定取正文原文当标题，不用目录的简写顶替。"),
         "finding": f_drop},
    ],
}

p = os.path.join(PLANS, "基线-数字人文.json")
json.dump(plan, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("写入", p)
