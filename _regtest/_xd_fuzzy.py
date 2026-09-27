# -*- coding: utf-8 -*-
"""模糊比对：印刷目录 52 条 vs 树里 L1/L2，找出所有标题不一致（截断/异体字）。"""
import json
import re
import sys
import difflib
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"C:\Users\Zhaoshuochen\Desktop\书籍\doclab")
SID = "现代档案与文件管理必读-法-瓦尔纳主编-国际档案理事会工作组编-孙钢等译-法-瓦尔纳主编-etc.e2ef74edc4"
WD = ROOT / "_work" / SID
sys.path.insert(0, str(ROOT))
from core import pagecal  # noqa: E402

TOC = [
    ("前言", 1), ("第一编 档案馆的作用", 1),
    ("档案馆的责任与档案工作者的职责", 3), ("中央及地方档案机构与社会的关系", 38),
    ("档案、发展及国家主权", 49), ("发展中国家的档案馆：对国家发展的贡献", 64),
    ("第二编 档案工作基本原则", 71), ("作为档案的历史手稿——几种定义及其应用", 73),
    ("档案管理工作中的来源原则——理论原则和实际问题", 82), ("关于档案整理原则的几点思考", 107),
    ("马克斯·雷曼与来源原则的创立", 112), ("档案工作新技术", 119),
    ("第三编 基本的法律问题", 127), ("档案立法", 129),
    ("关于文件从某些国家领土内的档案馆向其形成国转让问题的研究报告", 135), ("政府部门中档案的地位", 145),
    ("第四编 档案人员的专业培训", 155), ("培训档案和文件管理人员的国际标准", 157),
    ("第五编 档案和文件的管理", 169), ("档案管理和文件管理的现代观念", 171),
    ("行政档案馆", 179), ("文件管理是档案工作的一项职责", 195),
    ("第六编 鉴定和处置", 207), ("处置政策在塞纳档案馆实践中的反映", 209),
    ("现代文件的鉴定", 241), ("销毁的艺术", 256), ("现代档案的筛选与鉴定", 264), ("档案鉴定的原则", 276),
    ("第七编 整理和著录", 289), ("美国档案的现代整理方法", 291), ("档案査找工具", 310),
    ("第八编 利用和咨询服务", 317), ("为利用者提供信息和指导工作的初步报告", 319),
    ("社会科学研究与保密问题", 336), ("信息开放和隐私：公民自由论者的困境", 342),
    ("档案的学术利用", 352), ("档案的利用政策从限制到开放", 369),
    ("第九编 展览、教育服务和公共关系", 375), ("档案馆与学校教育—可能性、问题及局限", 377),
    ("利用档案进行教学", 388), ("档案与公共关系", 396), ("档案与文化", 402),
    ("第十编 档案的保护", 417), ("从马来西亚的特殊情况看文件的保护工作", 419),
    ("热带国家的档案馆建筑和设施", 435), ("档案的保护技术", 452),
    ("第十一编 现代技术与档案", 463), ("档案缩微标准化计划要点", 465), ("新的情报技术和档案", 489),
    ("第十二编 联合国教科文组织与档案发展", 497), ("联合国教科文组织与档案发展", 499),
    ("国际档案理事会及其所取得的成就和对未来的展望", 519),
]

def norm(s):
    s = re.sub(r"[①-⑳]", "", s or "")
    s = re.sub(r"\s+", "", s)
    for a, b in (("査", "查"), ("（", "("), ("）", ")"), ("，", "、"), ("－", "—")):
        s = s.replace(a, b)
    return s

oc = json.loads((WD / "outline.json").read_text(encoding="utf-8"))
cal = pagecal.load_calibration(WD)
flat = []
def walk(ns):
    for n in ns:
        flat.append(n)
        walk(n.get("children") or [])
walk(oc["tree"])
nodes = [n for n in flat if n.get("level") in (1, 2) and not str(n.get("title") or "").startswith("前置")]
print(f"目录 {len(TOC)} 条 / 树 L1+L2（去前置）{len(nodes)} 条\n")

tnorm = [norm(n.get("title")) for n in nodes]
print("=== 标题不一致（模糊匹配 < 0.97）===")
bad = []
for t, p in TOC:
    nt = norm(t)
    best = max(range(len(tnorm)), key=lambda i: difflib.SequenceMatcher(None, nt, tnorm[i]).ratio())
    r = difflib.SequenceMatcher(None, nt, tnorm[best]).ratio()
    if r < 0.97:
        n = nodes[best]
        loc = cal.locator(n.get("shard"), int(n.get("page_idx") or 0))
        bad.append((t, p, n.get("title"), round(r, 3), n.get("key"), loc))
        print(f"  目录={t!r}({p})   树={n.get('title')!r}  相似={r:.3f} key={n.get('key')} loc={loc}")
if not bad:
    print("  无")

print("\n=== 页码不一致 ===")
for i, (t, p) in enumerate(TOC):
    if i >= len(nodes):
        break
    n = nodes[i]
    loc = cal.locator(n.get("shard"), int(n.get("page_idx") or 0))
    m = re.search(r"(\d+)", str(loc or ""))
    got = int(m.group(1)) if m else None
    if got != p:
        print(f"  [{i}] 目录={t!r}({p}) 树={n.get('title')!r} loc={loc}")
print("   （无输出＝逐条页码全中）")
