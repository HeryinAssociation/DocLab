# -*- coding: utf-8 -*-
"""生成四本书的修订单（JSON），供 doclab fix --plan 演练 / --apply 落盘。

每条都带 evidence：凭哪条发现、哪个块、原文写了什么。
"""
import os
import sys
import json

sys.stdout.reconfigure(encoding="utf-8")
BASE = r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab"
PLANS = os.path.join(BASE, "_regtest", "_plans")
os.makedirs(PLANS, exist_ok=True)

HX = "魂系历史主义-西方档案学支柱理论发展研究-Pdg2Pic-黄霄羽著.8701935468"
XD = "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"
DH = "档案与社会记忆研究_丁华东.b8a64398d9"
HH = "中国档案学的理念与模式-胡鸿杰著-Hu-Hong-Jie-Zhu-胡鸿杰-1958--胡鸿杰著-.60ddbd4329"


def tree_index(sid):
    o = json.load(open(os.path.join(BASE, "_work", sid, "outline.json"), encoding="utf-8"))
    rows = []

    def walk(ns):
        for n in ns:
            rows.append(n)
            walk(n.get("children") or [])
    walk(o["tree"])
    return o, rows


def snap(n):
    kids = sum(int(c.get("n_chars") or 0) for c in (n.get("children") or []))
    return {"title": n["title"], "level": n["level"], "marker": n.get("marker"),
            "shard": n.get("shard"), "page_idx": n.get("page_idx"),
            "chars": int(n.get("n_chars") or 0),
            "own_chars": int(n.get("n_chars") or 0) - kids,
            "blocks": int(n.get("n_blocks") or 0), "gid": n.get("gid_start")}


def by_key(rows, key):
    hit = [n for n in rows if str(n.get("key")) == str(key)]
    if not hit:
        raise SystemExit(f"找不到 key={key}")
    return hit[0]


# ---------------------------------------------------------------- 魂系
o, rows = tree_index(HX)
items = []
for k, why in [("488", "正文块只认出「第四章 来源原则展中遭受冲击」，OCR 掉了「发」字；"
                       "同一 gid 上已有目录背书的完整合成节点 toc:P1:488:…（「来源原则发展中遭受冲击」）"),
               ("1231", "正文块只认出「第七章 文件生命期理论“笑对挑战”」，OCR 掉了「周期」；"
                        "同一 gid 上已有目录背书的完整合成节点 toc:P1:1231:…"),
               ("485", "「西方档案学两大支柱理论的并立—来源原则发展中遭受冲击，文件生命周期理论产生"
                       "与盛行」是 gid 484「第二部分」的副题续行，被 OCR 标成了独立章标题")]:
    n = by_key(rows, k)
    items.append({"action": "delete", "key": k, "snapshot": snap(n), "evidence": why})
items.append({"action": "add", "gid": 1654, "title": "结束语", "level": 1, "offset": 0,
              "evidence": "书内目录列了「结束语」（印刷 227）；正文 gid=1654（P2 物理 42）是 "
                          "MinerU 已标为标题的独立块，原文即「结束语」；R2 因「结束语」三字不在 "
                          "backmatter 词表里而漏收"})
items.append({"action": "levels", "pairs": {"991": 2},
              "evidence": "书内目录列了「第六章 来源原则被“重新发现”」（印刷 131）；正文该处 "
                          "gid=991（P1 物理 146，与目录页码一致）是 MinerU 标记的标题块，原文只剩 "
                          "「“重新发现”」（编号「第六章 来源原则被」被 OCR 吃掉）。它现被定为 4 级、"
                          "附在第四章之下，与第六章应有的章级不符"})
json.dump({"source_id": HX, "name": "魂系：删除三个残缺/误标标题、补回结束语、第六章定级",
           "note": "同一 gid 上「真块残缺 vs 目录节点完整」一律保留目录节点（书自己的目录是权威"
                   "标题清单）。第六章 gid=991 的编号在正文里已丢失，只做定级、不改写标题。"
                   "第三部分/第四部分在正文与目录里都是折行残缺（「…完善——来源原则被」），"
                   "无完整原文可依，**不补**。",
           "items": items},
          open(os.path.join(PLANS, "魂系.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)

# ---------------------------------------------------------------- 现代档案
items = []
for gid, title, why in [
    (3425, "8.非洲成员国档案和文件管理系统和服务的需要研究",
     "audit numbering_gap「7. → 9.」缺 8.；正文 gid=3425（P3 物理 125）是独立成段的原文，"
     "夹在 7.（gid 3423）与 9.（gid 3427）之间，MinerU 只是没给它 text_level"),
    (3442, "3.阿克拉（加纳）、达喀尔（塞内加尔）地区档案工作者培训中心专题讲座和奖学金条例",
     "audit numbering_gap「2. → 4.」缺 3.；正文 gid=3442（P3 物理 126）独立成段，"
     "在 2.（gid 3440）与 4.（gid 3444）之间"),
    (3569, "3.进一步发展档案工作的措施（特别是在专业训练和高级培训方面）",
     "audit numbering_gap「2. → 4.」缺 3.；正文 gid=3569（P3 物理 149）独立成段，"
     "在 2.（gid 3560）与 4.（gid 3575）之间"),
]:
    items.append({"action": "add", "gid": gid, "title": title, "level": 3, "offset": 0,
                  "evidence": why})
json.dump({"source_id": XD, "name": "现代档案：补回三条 MinerU 未标标题的编号小节",
           "note": "另有两组跳号（2.3 的 3.–7.、4.2 的 28./29.）在正文里**没有**独立标题："
                   "前者前后是连续正文段、没有任何编号行；后者的编号与正文连写在同一块里"
                   "（「28.依照上述原则和方针，…」）。都无独立标题可立，按「不许编造」不补。",
           "items": items},
          open(os.path.join(PLANS, "现代档案.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)

# ---------------------------------------------------------------- 丁华东
o, rows = tree_index(DH)
items = []
PAIRS = [(233, 234, "社会记忆理论与档案记忆研究的学术坐标", "第一章", 23),
         (690, 691, "档案：社会记忆的形态", "第二章", 69),
         (1135, 1136, "档案与社会记忆传承", "第三章", 120),
         (1578, 1579, "档案与社会记忆建构", "第四章", 165),
         (2031, 2032, "档案与社会记忆控制", "第五章", 213),
         (2516, 2517, "档案的记忆能量分析", "第六章", 265),
         (2934, 2935, "档案记忆与现代传媒展演", "第七章", 309),
         (3333, 3334, "档案工作与社会记忆构筑", "第八章", 355)]
for bare, ttl_gid, title, num, printed in PAIRS:
    n = by_key(rows, str(bare))
    items.append({"action": "delete", "key": str(bare), "snapshot": snap(n),
                  "evidence": f"正文里这一章占两个块：gid={bare} 是「{num}」（MinerU 标为标题），"
                              f"gid={ttl_gid} 是标题正文「{title}」，被当成了普通段落。"
                              f"只留编号那一块，树里这条章的名字就是残缺的"})
    items.append({"action": "add", "gid": ttl_gid, "title": title, "level": 1, "offset": 0,
                  "evidence": f"书内目录列「{num} {title}」（印刷 {printed}）；正文 gid={ttl_gid} "
                              f"的原文即「{title}」，是标题的第二行；立断点后与 gid={bare} 的编号"
                              f"在正文里并存，正文一字不动"})
json.dump({"source_id": DH, "name": "丁华东：八个章标题由「只有编号」补成完整标题",
           "note": "八章在正文里都被 OCR 拆成「编号」与「标题正文」两块。处置是删掉只有编号的"
                   "那个节点、在标题正文那一块立断点 —— 标题取正文原文，与编号块在正文中并存。",
           "items": items},
          open(os.path.join(PLANS, "丁华东.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)

# ---------------------------------------------------------------- 胡鸿杰
o, rows = tree_index(HH)
items = []
for k, why in [("351", "gid=351 的真块是「档案学生成背景」（被 OCR 漏了编号）；同 gid 上已有目录"
                       "背书的合成节点「第一章 档案学的生成背景」"),
               ("720", "gid=720 的真块是「档案学 实践基础」；同 gid 上已有合成节点「第三章 档案学的实践基础」"),
               ("1038", "gid=1038 的真块是「第五章 档案学“基干体”一档案管理学」（破折号被认成「一」）；"
                        "同 gid 上已有合成节点「第五章 档案学的“基干体”档案管理学」"),
               ("1168", "gid=1168 的真块是「第六章 档案学“衍生群”（上）—档案文献编纂学」；"
                        "同 gid 上已有合成节点"),
               ("1477", "gid=1477 的真块是「第八章 档案学“终极者”——档案学概论」；同 gid 上已有合成节点"),
               ("1608", "gid=1608 的真块是「第九章 中国档案学评价机制」（目录作「中国档案学的评价机制」）；"
                        "同 gid 上已有合成节点"),
               ("554", "「档案学」是「第二章 档案学的理论条件」标题的前半，被 OCR 拆成独立标题块"),
               ("555", "「理论条件」是同一标题的后半，也被拆成了独立标题块"),
               ("882", "gid=882 的真块只剩编号「第四章」，标题正文在 gid=883「档案学学者构成」（MinerU 已标标题）"),
               ("1022", "gid=1022 的真块只剩编号「第二编」，编名在 gid=1023「中国档案学的结构功能」"),
               ("1594", "gid=1594 的真块只剩编号「第三编」，编名在 gid=1596「中国档案学的价值」")]:
    n = by_key(rows, k)
    items.append({"action": "delete", "key": k, "snapshot": snap(n), "evidence": why})
for gid, title, why in [
    (554, "第二章 档案学的理论条件",
     "audit numbering_gap「第一章 → 第三章」缺第二章；书内目录列「第二章 档案学的理论条件」"
     "（印刷 63）；正文里这一标题被 OCR 拆成 gid=554「档案学」与 gid=555「理论条件」两块，"
     "R5 因「理论条件」四字是目录标题的子串而误判为已存在、没有补。标题取目录行原文"),
    (883, "档案学学者构成",
     "正文 gid=883 是标题正文（MinerU 已标 text_level），删掉只剩编号的 gid=882 后在此立断点，"
     "标题取正文原文"),
    (1023, "中国档案学的结构功能",
     "正文 gid=1023 是编名（MinerU 已标 text_level）；标题取正文原文"),
    (1596, "中国档案学的价值",
     "正文 gid=1596 是编名；标题取正文原文"),
]:
    items.append({"action": "add", "gid": gid, "title": title, "level": 1, "offset": 0,
                  "evidence": why})
json.dump({"source_id": HH, "name": "胡鸿杰：章/编标题由残缺真块换成完整标题，补回第二章",
           "note": "凡「残缺真块 + 同 gid 目录节点」成对出现的，删残缺真块、留目录节点。"
                   "第二章标题在正文里被拆成「档案学」「理论条件」两块，两块都删、按目录行原文"
                   "在 gid=554 立断点。",
           "items": items},
          open(os.path.join(PLANS, "胡鸿杰.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)

print("plans written to", PLANS)
