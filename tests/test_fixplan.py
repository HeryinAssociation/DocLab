#!/usr/bin/env python
"""修订单的回归测试：闸门必须真的挡住，落盘必须只动人工核定层。

    python tests/test_fixplan.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import fixplan, manual                        # noqa: E402
from core.util import read_json, write_json             # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
PASS, FAIL = [], []


def ck(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {extra}" if extra and not cond else ""))


def make_wd() -> Path:
    wd = Path(tempfile.mkdtemp(prefix="doclab-fix-"))
    write_json(wd / "outline.json", {
        "source_id": "fx", "node_total": 3,
        "tree": [
            {"nid": "1", "level": 1, "title": "前置", "key": "0", "gid_start": 0,
             "shard": "P1", "page_idx": 0, "n_chars": 10, "n_blocks": 1, "marker": "front",
             "children": []},
            {"nid": "2", "level": 1, "title": "第一部分", "key": "10", "gid_start": 10,
             "shard": "P1", "page_idx": 5, "n_chars": 900, "n_blocks": 9, "marker": "part",
             "children": [
                 {"nid": "2.1", "level": 2, "title": "一、甲", "key": "12",
                  "gid_start": 12, "shard": "P1", "page_idx": 6, "n_chars": 400,
                  "n_blocks": 4, "marker": "dun", "children": []},
                 {"nid": "2.2", "level": 2, "title": "三、乙", "key": "14",
                  "gid_start": 14, "shard": "P1", "page_idx": 8, "n_chars": 400,
                  "n_blocks": 4, "marker": "dun", "children": []},
             ]},
        ],
    })
    return wd


def expect_reject(wd, plan, needle, label):
    try:
        fixplan.validate(plan, wd)
        ck(label, False, "竟然通过了")
    except fixplan.PlanError as e:
        ck(label, needle in str(e), f"错误信息：{e}")


def main() -> int:
    wd = make_wd()

    print("[闸门]")
    expect_reject(wd, {"items": []}, "没有 items", "空修订单拒收")
    expect_reject(wd, {"items": [{"action": "set-level", "key": "12", "level": 2,
                                  "evidence": "x"}]}, "不认识的 action",
                  "未知 action 拒收")
    expect_reject(wd, {"items": [{"action": "level", "key": "12", "level": 2}]},
                  "没有 evidence", "缺 evidence 拒收")
    expect_reject(wd, {"items": [{"action": "level", "key": "12", "level": 2,
                                  "evidence": "   "}]}, "没有 evidence",
                  "evidence 全空白也拒收")
    expect_reject(wd, {"items": [{"action": "delete", "key": "12", "evidence": "x",
                                  "snapshot": {"level": 2}}]}, "缺 title",
                  "delete 快照缺 title 拒收")
    expect_reject(wd, {"items": [{"action": "add", "gid": "toc:P1:19:某",
                                  "title": "t", "level": 2, "evidence": "x"}]},
                  "必须是真实块号", "add 落在非真实块上拒收")
    expect_reject(wd, {"items": [{"action": "level", "level": 2, "evidence": "x"}]},
                  "缺少字段 key", "缺主键拒收")

    print("\n[警告（不阻断）]")
    _, warns = fixplan.validate({"items": [
        {"action": "level", "key": "99999", "level": 2, "evidence": "e"},
        {"action": "delete", "key": "12", "evidence": "e",
         "snapshot": {"title": "某", "level": 2}},
    ]}, wd)
    ck("未知 key 只警告", any("不在当前目录树里" in w for w in warns), str(warns))
    ck("快照缺 gid 只警告", any("缺 gid" in w for w in warns), str(warns))

    print("\n[应用]")
    before_outline = (wd / "outline.json").read_text(encoding="utf-8")
    plan = {
        "source_id": "fx", "name": "测试单", "note": "跑一遍",
        "items": [
            {"action": "level", "key": "12", "level": 2,
             "evidence": "书内目录第 2 页平级列出", "finding": "A-001"},
            {"action": "levels", "pairs": {"12": 2, "14": 2},
             "evidence": "两条同档"},
            {"action": "anchor", "shard": "P1", "page_idx": 20, "printed": 8,
             "evidence": "目录第 3 页列出该章在第 3 页，正文落在物理页 15"},
            {"action": "add", "gid": 10, "title": "二、补的", "level": 2, "offset": 0,
             "evidence": "正文 gid=10 起首独立成段为原文标题"},
            {"action": "delete", "key": "99999", "evidence": "页眉残片",
             "snapshot": {"title": "残片", "level": 2, "marker": "dun", "shard": "P1",
                          "page_idx": 7, "chars": 0, "own_chars": 0, "blocks": 0,
                          "gid": 99999}},
        ],
    }
    rec = fixplan.apply(plan, wd, "fx")
    ck("应用条数正确", rec["applied"] == 5, str(rec["applied"]))
    ck("无失败", not rec["failed"], str(rec["failed"]))
    ck("锚点动过 → 标记需要重算页码", rec["needs_pagecal"] is True)

    ed = read_json(wd / "manual_edits.json", {})
    ck("levels 写入", ed["levels"].get("12") == 2, json.dumps(ed.get("levels")))
    ck("add 写入（键是 gid）", "10" in ed["added"], json.dumps(ed.get("added")))
    ck("delete 写入且带快照", ed["deleted"]["99999"]["title"] == "残片")
    ck("anchor 写入", ed["anchors"]["P1"][0] == {"page_idx": 20, "printed": 8},
       json.dumps(ed.get("anchors")))
    ck("人工定级与已删不共存（删掉的 key 不留 levels）",
       "99999" not in ed["levels"])

    print("\n[不越界]")
    ck("outline.json 一字未动",
       (wd / "outline.json").read_text(encoding="utf-8") == before_outline)
    ck("没生成 page_calibration.json（未跑重算）",
       not (wd / "page_calibration.json").exists())
    ck("修订记录落盘", (wd / "修订记录").is_dir()
       and len(list((wd / "修订记录").glob("*.json"))) == 1)
    ck("人读修订日志落盘且含依据",
       (wd / "校核修订记录.md").is_file()
       and "依据：" in (wd / "校核修订记录.md").read_text(encoding="utf-8"))

    print("\n[追加而非覆盖]")
    fixplan.apply({"items": [{"action": "level", "key": "14", "level": 3,
                              "evidence": "第二次修订"}]}, wd, "fx")
    log = (wd / "校核修订记录.md").read_text(encoding="utf-8")
    ck("第二次修订是追加（首次的依据还在）",
       log.count("依据：") >= 5 and "第二次修订" in log and "书内目录第 2 页平级列出" in log)

    print("\n[模板]")
    t = fixplan.template()
    ck("模板可解析且有 items", isinstance(t.get("items"), list) and t["items"])
    ck("模板每条都带 evidence", all(i.get("evidence") for i in t["items"]))

    print("\n[幂等]")
    def strip(d):
        """去掉时间戳再比 —— 已删快照里记的 `at` 每次都不同，那是**该**不同的。"""
        if isinstance(d, dict):
            return {k: strip(v) for k, v in d.items() if k not in ("at", "updated_at")}
        if isinstance(d, list):
            return [strip(x) for x in d]
        return d

    fixplan.apply(plan, wd, "fx")
    d1 = strip(read_json(wd / "manual_edits.json", {}))
    fixplan.apply(plan, wd, "fx")
    d2 = strip(read_json(wd / "manual_edits.json", {}))
    ck("同一单连跑两次结果一致", d1 == d2, f"{d1} != {d2}")

    print(f"\n{'='*60}\n通过 {len(PASS)}／失败 {len(FAIL)}")
    if FAIL:
        print("失败项：" + "；".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
