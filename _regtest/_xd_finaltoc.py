# -*- coding: utf-8 -*-
"""终局核对：印刷目录的 12 编 + 39 章 逐条对树里 L1/L2，含页码与标题。"""
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab")
SID = "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"
WD = ROOT / "_work" / SID
sys.path.insert(0, str(ROOT))
from core import pagecal  # noqa: E402

oc = json.loads((WD / "outline.json").read_text(encoding="utf-8"))
cal = pagecal.load_calibration(WD)

# 1) 解析印刷目录（gid=60 的原文，已手工誊录为结构化）
TOC = """
前言::1
第一编 档案馆的作用::1
档案馆的责任与档案工作者的职责::3
中央及地方档案机构与社会的关系::38
档案、发展及国家主权::49
发展中国家的档案馆：对国家发展的贡献::64
第二编 档案工作基本原则::71
作为档案的历史手稿——几种定义及其应用::73
档案管理工作中的来源原则——理论原则和实际问题::82
关于档案整理原则的几点思考::107
马克斯·雷曼与来源原则的创立::112
档案工作新技术::119
第三编 基本的法律问题::127
档案立法::129
关于文件从某些国家领土内的档案馆向其形成国转让问题的研究报告::135
政府部门中档案的地位::145
第四编 档案人员的专业培训::155
培训档案和文件管理人员的国际标准::157
第五编 档案和文件的管理::169
档案管理和文件管理的现代观念::171
行政档案馆::179
文件管理是档案工作的一项职责::195
第六编 鉴定和处置::207
处置政策在塞纳档案馆实践中的反映::209
现代文件的鉴定::241
销毁的艺术::256
现代档案的筛选与鉴定::264
档案鉴定的原则::276
第七编 整理和著录::289
美国档案的现代整理方法::291
档案査找工具::310
第八编 利用和咨询服务::317
为利用者提供信息和指导工作的初步报告::319
社会科学研究与保密问题::336
信息开放和隐私：公民自由论者的困境::342
档案的学术利用::352
档案的利用政策从限制到开放::369
第九编 展览、教育服务和公共关系::375
档案馆与学校教育—可能性、问题及局限::377
利用档案进行教学::388
档案与公共关系::396
档案与文化::402
第十编 档案的保护::417
从马来西亚的特殊情况看文件的保护工作::419
热带国家的档案馆建筑和设施::435
档案的保护技术::452
第十一编 现代技术与档案::463
档案缩微标准化计划要点::465
新的情报技术和档案::489
第十二编 联合国教科文组织与档案发展::497
联合国教科文组织与档案发展::499
国际档案理事会及其所取得的成就和对未来的展望::519
"""

def norm(s):
    s = re.sub(r"\s+", "", s or "")
    s = s.replace("査", "查").replace("（", "(").replace("）", ")")
    return s

toc = [(t, int(p)) for t, p in (ln.split("::") for ln in TOC.strip().splitlines())]

# 2) 树里的 L1/L2（文档序）
flat = []
def walk(ns, d):
    for n in ns:
        flat.append((d, n))
        walk(n.get("children") or [], d + 1)
walk(oc["tree"], 1)
tree = [(n["level"], n.get("title") or "", n) for d, n in flat if n.get("level") in (1, 2)]

print(f"印刷目录 {len(toc)} 条；树里 L1+L2 {len(tree)} 条\n")

# 3) 逐条按顺序比
rows = []
ti = 0
for lvl, title, n in tree:
    if norm(title).startswith("前置"):
        continue
    exp = toc[ti] if ti < len(toc) else None
    if exp is None:
        rows.append((lvl, title, n, None, "多"))
        continue
    same = norm(title) == norm(exp[0])
    loc = None
    try:
        loc = cal.locator(n.get("shard"), int(n.get("page_idx") or 0))
    except Exception:  # noqa: BLE001
        loc = None
    num = exp[1]
    locnum = None
    if loc:
        m = re.search(r"(\d+)", str(loc))
        locnum = int(m.group(1)) if m else None
    ok = "✓" if same else "✗"
    if locnum is not None and locnum != num:
        ok += f" 页码 ✗ 树={locnum} 目录={num}"
    rows.append((lvl, title, n, exp, ok))
    if same:
        ti += 1

for lvl, title, n, exp, ok in rows:
    tgt = f"{exp[0]}({exp[1]})" if exp else "—"
    print(f"  {'L'+str(lvl)} {ok:<22} 树={title!r:<42} 目录={tgt}")

print(f"\n已对上 {ti}/{len(toc)} 条")
print("树里未消费的 L1/L2：")
for lvl, title, n, exp, ok in rows:
    if exp is None or ok.startswith("✗"):
        print(f"  L{lvl} key={n.get('key')} {title!r} ← 期望 {exp}")
