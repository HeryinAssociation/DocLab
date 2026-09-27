#!/usr/bin/env python
"""目录 JSON 往返的回归测试：导出形状、diff 翻译规则、拒绝路径。

    python tests/test_tocio.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import tocio                                   # noqa: E402
from core.util import read_json, write_json             # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
PASS, FAIL = [], []


def ck(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {extra}" if extra and not cond else ""))


def make_wd() -> Path:
    wd = Path(tempfile.mkdtemp(prefix="doclab-tocio-"))
    write_json(wd / "outline.json", {
        "source_id": "fx", "node_total": 6,
        "tree": [
            {"nid": "1", "level": 0, "title": "前置（封面·书名页·版权页）", "key": "-1",
             "gid_start": -1, "shard": "P1", "page_idx": 0, "n_chars": 10, "n_blocks": 1,
             "marker": "front", "children": []},
            {"nid": "2", "level": 1, "title": "第一章 绪论", "key": "10",
             "gid_start": 10, "shard": "P1", "page_idx": 5, "n_chars": 900, "n_blocks": 9,
             "marker": "chapter", "children": [
                 {"nid": "2.1", "level": 2, "title": "一、甲", "key": "12",
                  "gid_start": 12, "shard": "P1", "page_idx": 6, "n_chars": 400,
                  "n_blocks": 4, "marker": "dun", "children": []},
                 {"nid": "2.2", "level": 2, "title": "三、乙", "key": "14",
                  "gid_start": 14, "shard": "P1", "page_idx": 8, "n_chars": 400,
                  "n_blocks": 4, "marker": "dun", "children": [
                      {"nid": "2.2.1", "level": 3, "title": "（一）丙", "key": "16",
                       "gid_start": 16, "shard": "P1", "page_idx": 9, "n_chars": 200,
                       "n_blocks": 2, "marker": "paren_zh", "children": []},
                  ]},
             ]},
            {"nid": "3", "level": 1, "title": "第二章 已人工新增的章", "key": "add:20",
             "gid_start": 20, "shard": "P1", "page_idx": 20, "n_chars": 100, "n_blocks": 1,
             "marker": "chapter", "flags": ["manual"], "offset": 0, "children": []},
        ],
    })
    write_json(wd / "project.json", {"doc_title": "测试书"})
    return wd


def main() -> int:
    wd = make_wd()
    real = {10, 12, 14, 16, 20, 30, 31}

    # ---------------------------------------------------------------- 导出
    exp = tocio.export_toc(wd, "fx", depth=3)
    ck("导出：深度内 6 条", exp["node_count"] == 6, str(exp["node_count"]))
    ck("导出：全书统计含全部", exp["node_total_all"] == 6)
    keys = [n["key"] for n in exp["nodes"]]
    ck("导出：文档顺序", keys == ["-1", "10", "12", "14", "16", "add:20"], str(keys))
    n10 = next(n for n in exp["nodes"] if n["key"] == "10")
    ck("导出：own_chars=自身−子代", n10["own_chars"] == 900 - 400 - 400, str(n10["own_chars"]))
    ck("导出：快照字段齐", all(k in n10 for k in
       ("title", "level", "marker", "shard", "page_idx", "gid", "blocks", "chars")))
    exp2 = tocio.export_toc(wd, "fx", depth=2)
    ck("导出：depth=2 不含 L3", all(n["level"] <= 2 for n in exp2["nodes"])
       and exp2["node_count"] == 5)

    # ---------------------------------------------------------------- 无改动
    d = tocio.diff_toc(wd, exp)
    ck("无改动：0 条", len(d["items"]) == 0, str(d["changes"]))
    ck("无改动：无错误", not d["errors"], str(d["errors"]))

    # depth=2 导出的 JSON 原样导回：深层条目不在集合内，不判删
    d = tocio.diff_toc(wd, exp2)
    ck("深度一致：L3 不误判删除", len(d["items"]) == 0 and not d["errors"], str(d["changes"]))

    # ---------------------------------------------------------------- level 变化
    mod = {**exp, "nodes": [{**n, **({"level": 1} if n["key"] == "12" else {})}
                            for n in exp["nodes"]]}
    d = tocio.diff_toc(wd, mod)
    ck("改级：出 level 动作", len(d["items"]) == 1
       and d["items"][0]["action"] == "level"
       and d["items"][0]["level"] == 1, str(d["items"]))

    # ---------------------------------------------------------------- 删除 / 前置
    mod = {**exp, "nodes": [n for n in exp["nodes"] if n["key"] != "14"]}
    d = tocio.diff_toc(wd, mod)
    ck("删条：出 delete 且快照带 title",
       len(d["items"]) == 1 and d["items"][0]["action"] == "delete"
       and d["items"][0]["snapshot"]["title"] == "三、乙", str(d["items"]))
    mod = {**exp, "nodes": [n for n in exp["nodes"] if n["marker"] != "front"]}
    d = tocio.diff_toc(wd, mod)
    ck("前置节点不可删", any("不可删" in e for e in d["errors"]), str(d["errors"]))

    # ---------------------------------------------------------------- 新增
    mod = {**exp, "nodes": exp["nodes"] + [
        {"key": "", "gid": 30, "title": "第三章 结论", "level": 1}]}
    d = tocio.diff_toc(wd, mod)
    ck("新增：出 add（gid+title+level）",
       len(d["items"]) == 1 and d["items"][0] == {
           "action": "add", "gid": 30, "title": "第三章 结论", "level": 1, "offset": 0,
           "evidence": d["items"][0]["evidence"]}, str(d["items"]))

    # ---------------------------------------------------------------- 拒绝路径
    orig = tocio._real_block_gids
    tocio._real_block_gids = lambda wd_: real
    try:
        mod = {**exp, "nodes": exp["nodes"] + [
            {"key": "", "gid": 999, "title": "瞎编的章", "level": 1}]}
        d = tocio.diff_toc(wd, mod)
        ck("新增：假 gid 拒收", any("不是这本书的正文块号" in e for e in d["errors"]),
           str(d["errors"]))
        mod = {**exp, "nodes": exp["nodes"] + [
            {"key": "", "gid": 30, "title": "缺级", "level": None}]}
        d = tocio.diff_toc(wd, mod)
        ck("新增：缺字段拒收", any("缺 gid / title / level" in e for e in d["errors"]))
    finally:
        tocio._real_block_gids = orig

    mod = {**exp, "nodes": [{**n, **({"title": "第一章 绪论（改）"} if n["key"] == "10" else {})}
                            for n in exp["nodes"]]}
    d = tocio.diff_toc(wd, mod)
    ck("真实块标题不许改", any("不许直接改" in e for e in d["errors"]), str(d["errors"]))

    mod = {**exp, "nodes": [{**n, **({"title": "第二章 已人工新增的章（补全）",
                                      "level": 1} if n["key"] == "add:20" else {})}
                            for n in exp["nodes"]]}
    d = tocio.diff_toc(wd, mod)
    ck("add: 条目改标题=覆盖 added",
       len(d["items"]) == 1 and d["items"][0]["action"] == "add"
       and d["items"][0]["gid"] == 20
       and d["items"][0]["title"] == "第二章 已人工新增的章（补全）", str(d["items"]))

    mod = {**exp, "nodes": [{**n, **({"level": 11} if n["key"] == "12" else {})}
                            for n in exp["nodes"]]}
    d = tocio.diff_toc(wd, mod)
    ck("level 越界拒收", any("越界" in e for e in d["errors"]))

    # ---------------------------------------------------------------- 导回落盘
    mod = {**exp, "note": "语义核对测试",
           "nodes": [n for n in exp["nodes"] if n["key"] not in ("14", "16")]
           + [{"key": "", "gid": 31, "title": "第三章 结论", "level": 1, "reason": "正文 gid=31 起首独立成段"}]}
    mod["nodes"][2] = {**mod["nodes"][2], "level": 3}      # 「一、甲」L2→L3
    r = tocio.import_toc(wd, "fx", mod, do_apply=True)
    ck("导回：应用成功", r["ok"] and r.get("applied") == 4, str(r))
    ed = read_json(wd / "manual_edits.json", {})
    ck("落盘：levels 有「一、甲」", ed["levels"].get("12") == 3, str(ed.get("levels")))
    ck("落盘：deleted 有「三、乙」（含 16）",
       "14" in ed["deleted"] and "16" in ed["deleted"], str(ed.get("deleted", {}).keys()))
    ck("落盘：added 有 gid=31",
       str(31) in ed["added"] and ed["added"]["31"]["title"] == "第三章 结论",
       str(ed.get("added")))
    ck("修订记录已留档", (wd / "修订记录").is_dir())

    print(f"\n{len(PASS)} 通过 / {len(FAIL)} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
