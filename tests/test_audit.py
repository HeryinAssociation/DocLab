#!/usr/bin/env python
"""目录校核的回归测试：用**合成夹具**逐条触发每个检查项。

为什么不用真书：这些检查要证明的是「该报的报得出来」，真书里恰好没有的情况
（比如正文里真的缺了第三章）测不到。合成夹具里刻意造出每一种病，跑完断言
check 名字都在。夹具里不出现任何具体书名。

    python tests/test_audit.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import audit                                  # noqa: E402
from core.util import write_json                        # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

PASS, FAIL = [], []


def ck(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {extra}" if extra and not cond else ""))


# ---------------------------------------------------------------- 夹具

def node(nid, level, title, gid, shard="P1", page_idx=0, marker="plain",
         n_chars=500, n_blocks=3, flags=None, children=None):
    return {"nid": nid, "level": level, "title": title, "marker": marker,
            "key": str(gid), "gid_start": gid, "gid_end": gid,
            "shard": shard, "page_idx": page_idx,
            "n_chars": n_chars, "n_blocks": n_blocks,
            "flags": flags or [], "children": children or []}


def seg(shard, kind, p0, p1, printed0, printed1, offset, n_obs=10, conf="high"):
    return {"shard": shard, "kind": kind, "page_idx_start": p0, "page_idx_end": p1,
            "printed_start": printed0, "printed_end": printed1, "offset": offset,
            "n_obs": n_obs, "span": p1 - p0 + 1,
            "coverage": 1.0, "confidence": conf, "basis_pages": [], "derived": False}


def fixture() -> tuple[dict, dict]:
    """造一棵带齐各种病的树 + 一份刻意有毛病的校准。

    分片 P1：物理 0-9 前置（印刷 1-10，offset +1）、物理 10-19 正文（印刷 1-10，
    offset −9）—— 两段的印刷区间**故意重叠**，这就是「前置/正文分界划错」的样子。
    """
    tree = [
        node("1", 1, "前置（封面·书名页·版权页）", 0, page_idx=0, marker="front",
             children=[], n_chars=100),
        node("2", 1, "第一部分 综述", 10, page_idx=10, marker="part", children=[
            # A 层级跳变：L1 → L3
            node("2.1", 3, "一、总论", 12, page_idx=10, marker="dun"),
            # B 编号跳号：一 → 三（缺「二、」），并给出 probe 搜索窗口
            node("2.2", 3, "三、分论", 14, page_idx=16, marker="dun", children=[
                # C 页码倒退（6 ▸ 5）+ D 父子倒挂（父 7 > 子 6/5）
                node("2.2.1", 4, "（一）甲", 15, page_idx=15, marker="paren_zh"),
                node("2.2.2", 4, "（二）乙", 16, page_idx=14, marker="paren_zh"),
            ]),
            # B2 同档重号
            node("2.3", 3, "三、又一条三", 18, page_idx=17, marker="dun"),
            # 同父同名重复
            node("2.4", 3, "四、小结", 19, page_idx=18, marker="dun"),
            node("2.5", 3, "四、小结", 20, page_idx=18, marker="dun"),
            # H 空节点
            node("2.6", 3, "六、空章", 22, page_idx=18, marker="dun",
                 n_chars=0, n_blocks=0),
            # 与印刷目录对得上、但页码差 5 页
            node("2.7", 3, "七、结论与展望", 23, page_idx=18, marker="dun"),
            # I 序号**回退**（七 → 二）：译文集里每篇自带编号的常态，
            #    该报「重新起算」而不是「缺号」
            node("2.8", 3, "二、另一篇", 24, page_idx=19, marker="dun"),
        ]),
        # E 章级（L1）落在前置编号区间 —— 前置/正文分界划错的信号
        node("3", 1, "第二部分 前沿", 30, page_idx=6, marker="part", children=[
            node("3.1", 2, "一、前沿总述", 31, page_idx=19, marker="dun"),
        ]),
    ]
    calib = {
        "source_id": "fixture", "shards": ["P1", "P2"],
        "shard_pages": {"P1": 30, "P2": 20},
        "segments": [
            seg("P1", "front", 0, 9, 1, 10, 1, 0, "manual"),
            seg("P1", "body", 10, 19, 1, 10, -9, 10, "high"),
            seg("P1", "body", 11, 11, 3, 3, -8, 1, "low"),
            # 与前面完全不相交、却拿到同一个页锚：物理 14 与 20 都 → 「5」。
            # 导出后两处页锚都写 `<!-- p=5 -->`，按锚回查会翻到另一页。
            seg("P1", "body", 20, 29, 5, 14, -15, 10, "high"),
            # P2：一条完全连续的正文序列（offset +20），中间夹一页被 OCR 读错的页码
            # （观测 120，按两侧应为 30）—— 这就是真书里「314 → 815 → 316」的样子。
            seg("P2", "body", 0, 9, 20, 29, 20, 10, "high"),
            seg("P2", "body", 10, 10, 120, 120, 110, 1, "low"),
            seg("P2", "body", 11, 19, 31, 39, 20, 9, "high"),
        ],
        "gaps": [{"shard": "P1", "after_page_idx": 3, "before_page_idx": 6,
                  "n_unmapped_pages": 2, "missing_printed": "5,6"}],
        "continuity": {"ok": False, "checks": [
            {"from": "P1", "to": "P2", "expected": 11, "next_printed": 13,
             "ok": False, "note": "差 2 页"},
            # 端点落在离群页上 → 属于同一根因，不该再单独报一条
            {"from": "P2:10", "to": "P2:11", "expected": 121, "next_printed": 31,
             "ok": False, "note": "倒退"}]},
        "header_conflicts": [], "locator_type": "paginated", "verdict": "manual",
        "reason": "夹具", "thresholds": {}, "observation_count": 0,
        "observations": [],
    }
    outline = {
        "source_id": "fixture", "doc_title": "夹具", "node_total": 14,
        "generated_at": "2026-01-01T00:00:00+08:00",
        "tree": tree,
        "level_stats": {},
        "printed_toc": {"found": True, "pages": [1], "shard": "P1", "heading_page": 1,
                        "entries": [
                            # 树里有、页码对不上（树里 9，目录 14）
                            {"title": "七、结论与展望", "printed": 14, "kind": "chapter"},
                            {"title": "第一部分 综述", "printed": None, "kind": "part"},
                            # 抽取没洗干净：点线页码还挂在标题上
                            {"title": "八、残条与点线 …… 99", "printed": 99,
                             "kind": "chapter"},
                            # 目录背书、树里没有 → 疑似漏标题
                            {"title": "九、目录背书却被丢弃", "printed": 7,
                             "kind": "chapter"},
                        ]},
        "toc_crosscheck": {"available": True, "toc_entry_count": 4, "matched": 1,
                           "match_rate": 0.2, "toc_coverage": 0.25,
                           "headings_not_in_toc": [], "toc_entries_not_detected": []},
        "toc_region": {"found": True, "pages": [1], "shard": "P1", "heading_page": 1},
        "toc_repair": [],
        "dropped_headings": [
            # 自带编号 → warn
            {"shard": "P1", "page_idx": 7, "reason": "不像标题（过长或带句末标点）",
             "text": "第九章 被误杀的一章"},
            # 印刷目录背书 → high
            {"shard": "P1", "page_idx": 8, "reason": "不像标题（过长或带句末标点）",
             "text": "九、目录背书却被丢弃——一个带副题的标题？"},
            # 纯长正文 → info
            {"shard": "P1", "page_idx": 8, "reason": "不像标题（过长或带句末标点）",
             "text": "这一段是很长的正文而不是标题，" * 10},
            {"shard": "P1", "page_idx": 0, "reason": "R2 位于前置区（封面/书名页/版权页）",
             "text": "夹具"},
        ],
        "manual_deleted_count": 0, "manual_level_count": 0,
        "running_texts": [], "unclassified_types": {},
    }
    return outline, calib


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="doclab-audit-"))
    outline, calib = fixture()
    write_json(tmp / "outline.json", outline)
    write_json(tmp / "page_calibration.json", calib)

    rep = audit.run("fixture", tmp, depth=3)
    got = {f["check"] for f in rep["findings"]}
    by = {}
    for f in rep["findings"]:
        by.setdefault(f["check"], []).append(f)

    print("校核 check 命中：", sorted(got))
    print("判定：", rep["verdict"], rep["severity"], "\n")

    print("[结构]")
    ck("level_jump 层级跳变", "level_jump" in got)
    ck("duplicate_title 同层同名", "duplicate_title" in got)

    print("[编号]")
    ck("numbering_gap 跳号", "numbering_gap" in got)
    ck("numbering_repeat 重号", "numbering_repeat" in got)
    g = (by.get("numbering_gap") or [{}])[0]
    p = g.get("probe") or {}
    ck("numbering_gap 带 probe 搜索窗口",
       p.get("kind") == "gap_between" and p.get("gid_range") == [12, 14],
       f"probe={p}")
    ck("numbering_gap 期望标题写成「二、」",
       (p.get("expect") or [""])[0] == "二、", f"expect={p.get('expect')}")
    # 「序号回退」与「缺号」必须分开：译文集里每篇自带编号，回退是常态。
    ck("numbering_restart 序号回退单列（warn）",
       "numbering_restart" in got
       and all(f["severity"] == "warn" for f in by.get("numbering_restart", [])),
       f"{[(f['title'], f['severity']) for f in by.get('numbering_restart', [])]}")
    ck("回退不再混进 numbering_gap（gap 恒为 high）",
       all("回退" not in f["title"] for f in by.get("numbering_gap", []))
       and all(f["severity"] == "high" for f in by.get("numbering_gap", [])))
    rr = (by.get("numbering_restart") or [{}])[0]
    ck("numbering_restart 的 probe 指向「是否换了篇」",
       (rr.get("probe") or {}).get("kind") == "restart_between"
       and "换了一篇" in ((rr.get("probe") or {}).get("hint") or ""),
       f"probe={rr.get('probe')}")
    # 回归：曾经把「第一章 → 第二章」这种连续号也算成重新起算，一本文集刷出上百条。
    ck("连续号（第一章→第二章）绝不判为重新起算",
       all(f["evidence"]["n"] != f["evidence"]["prev_n"] + 1
           for f in by.get("numbering_restart", [])),
       f"{[(f['evidence']['prev_n'], f['evidence']['n']) for f in by.get('numbering_restart', [])]}")

    print("[页码]")
    ck("page_decreasing 页码倒退", "page_decreasing" in got)
    ck("page_parent_after_child 父子倒挂", "page_parent_after_child" in got)
    ck("page_zone_crossing 跨编号区间", "page_zone_crossing" in got)
    ck("top_node_in_front_zone 章级落前置区", "top_node_in_front_zone" in got)

    print("[印刷目录]")
    ck("toc_entry_noisy 条目残留点线", "toc_entry_noisy" in got)
    ck("toc_page_mismatch 页码对不上", "toc_page_mismatch" in got)
    ck("toc_entry_undetected 目录有树里没有", "toc_entry_undetected" in got)
    u = (by.get("toc_entry_undetected") or [{}])[0]
    ck("toc_entry_undetected 带搜索窗口",
       bool((u.get("probe") or {}).get("expect")), f"probe={u.get('probe')}")

    print("[被丢弃的候选]")
    ck("dropped_toc_backed 目录背书却被弃（high）",
       any(f["severity"] == "high" for f in by.get("dropped_toc_backed", [])))
    ck("dropped_numbered_heading 自带编号疑似误杀",
       "dropped_numbered_heading" in got)
    ck("dropped_long_candidate 长正文（info）",
       all(f["severity"] == "info" for f in by.get("dropped_long_candidate", [{}])))

    print("[校准]")
    ck("calib_segment_overlap 印刷区间重叠", "calib_segment_overlap" in got)
    ck("calib_short_segment 极短区段", "calib_short_segment" in got)
    ck("calib_continuity 跨片接续异常", "calib_continuity" in got)
    ck("calib_declared_gap 已声明断点", "calib_declared_gap" in got)
    ck("无 calib_missing（校准在）", "calib_missing" not in got)

    # 页码类问题必须**收成根因**：同一件事说 30 遍，读者会以为有 30 个错。
    print("[页码根因归并]")
    ck("page_obs_outlier 单页离群（high）", "page_obs_outlier" in got)
    o = (by.get("page_obs_outlier") or [{}])[0]
    oe = o.get("evidence") or {}
    ck("离群页给出「按两侧序列应为」",
       oe.get("pages") == [10] and (oe.get("items") or [{}])[0].get("expected") == 30
       and (oe.get("items") or [{}])[0].get("observed") == 120,
       f"items={oe.get('items')}")
    ck("离群页带取证窗口 probe",
       (o.get("probe") or {}).get("kind") == "page_obs"
       and (o.get("probe") or {}).get("physical", {}).get("page_idx") == 10,
       f"probe={o.get('probe')}")
    ck("离群页造成的区间重叠 → 不重复报",
       not any(any((f.get("evidence", {}).get(k) or {}).get("shard") == "P2"
                   and (f.get("evidence", {}).get(k) or {}).get("page_idx_start") == 10
                   for k in ("a", "b"))
               for f in by.get("calib_segment_overlap", [])))
    ck("离群页的极短区间 → 不重复报",
       not any(f.get("evidence", {}).get("shard") == "P2"
               and f.get("evidence", {}).get("page_idx_start") == 10
               for f in by.get("calib_short_segment", [])))
    ck("端点落在离群页的接续异常 → 不重复报",
       not any(str((f.get("evidence") or {}).get("from", "")).startswith("P2:10")
               for f in by.get("calib_continuity", [])))
    ck("与离群页无关的接续异常仍照报",
       any(str((f.get("evidence") or {}).get("from", "")) == "P1"
           for f in by.get("calib_continuity", [])))

    print("[页锚歧义]")
    ck("page_locator_collision 定位符撞车", "page_locator_collision" in got)
    coll = by.get("page_locator_collision", [])
    ck("撞车一律报出，正文区判 high",
       bool(coll) and any(f["severity"] == "high" for f in coll),
       f"{[(f['evidence']['locator'], f['severity']) for f in coll]}")
    ck("severity 与 zone 对应（前置区 warn）",
       all((f["severity"] == "warn") == ((f.get("evidence") or {}).get("zone") == 1)
           for f in coll),
       f"{[(f['evidence']['locator'], f['severity']) for f in coll]}")

    # 关注深度：「只看 L3」时，L4 的问题不该还是高危 —— 它不影响导出，但也不能消失。
    rep2 = audit.run("fixture", tmp, depth=2)
    by2 = {}
    for f in rep2["findings"]:
        by2.setdefault(f["check"], []).append(f)
    deep = [f for f in by2.get("page_parent_after_child", [])
            if (f.get("scope") or "").count(".") >= 2]
    ck("depth=2：L3+ 的发现降级为非 high（但仍报出）",
       bool(deep) and all(f["severity"] != "high" for f in deep),
       f"{[(f['scope'], f['severity']) for f in deep]}")

    print("[契约]")
    ck("所有 finding 都有 id/check/severity",
       all(f.get("id") and f.get("check") and f.get("severity")
           for f in rep["findings"]))
    ck("id 唯一", len({f["id"] for f in rep["findings"]}) == len(rep["findings"]))
    ck("high 排在 warn 前",
       [f["severity"] for f in rep["findings"]]
       == sorted([f["severity"] for f in rep["findings"]],
                 key=lambda s: {"high": 0, "warn": 1, "info": 2}[s]))
    ck("落盘 目录校核.json / .md",
       (tmp / "目录校核.json").is_file() and (tmp / "目录校核.md").is_file())
    ck("不改动 outline.json / page_calibration.json",
       (tmp / "outline.json").read_text(encoding="utf-8")
       == write_json(tmp / "outline.json", outline).read_text(encoding="utf-8"))
    ck("outline_flat 只到关注深度", all(r["level"] <= 3 for r in rep["outline_flat"]))

    print("\n[阈值]")
    ck("verdict 因高危为 fail", rep["verdict"] == "fail")
    ck("severity 计数与 findings 一致",
       rep["severity"]["high"] == sum(1 for f in rep["findings"]
                                      if f["severity"] == "high"))

    print(f"\n{'='*60}\n通过 {len(PASS)}／失败 {len(FAIL)}")
    if FAIL:
        print("失败项：" + "；".join(FAIL))
    print("夹具目录：" + str(tmp))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
