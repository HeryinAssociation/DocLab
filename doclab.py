#!/usr/bin/env python
"""doclab —— 文献处理工作台（原型）

    python doclab.py probe                        环境与 MinerU API 自检
    python doclab.py adopt --dirs <工程目录>...     登记本机已有的 MinerU 工程
    python doclab.py ingest <PDF>                 调 API 做 OCR（超限自动分片）
    python doclab.py ingest <EPUB>                本机解析 EPUB，不调 MinerU、不烧配额
    python doclab.py ingest <PDF> --shard-mode local   前置切块改由本机 pypdf 做
    python doclab.py resume --source <source_id>  续跑没落地的 OCR（只补未就绪的片）
    python doclab.py remove <source_id>           移出工作台（产物进 _trash，可恢复）
    python doclab.py outline <source_id>          构建目录索引（先看，再选深度）
    python doclab.py audit <source_id>            目录校核（结构/编号/页码/印刷目录，只读）
    python doclab.py fix <source_id> --plan P     按修订单改人工核定层（默认演练）

  目录核对（JSON 往返）：
    python doclab.py toc-export <source_id> -d 2  目录结构导出为 JSON（-d=核对深度，更深层不导出）
    python doclab.py grep <source_id> --q 第三章 --json  正文块检索（找漏掉的标题，拿 gid）
    python doclab.py toc-import <source_id> --file 改后.json --apply  把 AI 改好的 JSON 导回（默认演练）

    python doclab.py pagecal <source_id>          页码校准
    python doclab.py export <source_id> -d 2      按第 2 层切分导出
    python doclab.py verify <source_id>           三道闸门校验
    python doclab.py run <source_id> -d 2         一次跑完 outline→pagecal→export→verify
    python doclab.py sources                      列出已登记的源
    python doclab.py status <source_id>           看某源的状态
    python doclab.py ui --port 8765               起本地界面

设计原则：逻辑只在这一层实现一次；MCP / UI 都是它的薄壳。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import __version__                                    # noqa: E402
from core.config import (load_config, work_dir, out_dir, WORK_DIR, UI_DIR,   # noqa: E402
                         Config, list_sources, resolve_source as _rs)
from core.util import force_utf8_stdout, read_json, write_json, write_text     # noqa: E402

force_utf8_stdout()

BANNER = f"doclab v{__version__}｜砚 · 文献处理工作台"


# ---------------------------------------------------------------- 辅助

def resolve_source(arg: str | None) -> str:
    try:
        return _rs(arg)
    except (FileNotFoundError, ValueError) as e:
        raise SystemExit(str(e))


def load_blocks(source_id: str):
    from core.project import load_shards, iter_blocks
    wd = work_dir(source_id)
    shards = load_shards(wd)
    blocks = iter_blocks(shards)
    return wd, shards, blocks


def _log(msg: str) -> None:
    print(msg, flush=True)


def fn_mode(wd: Path, arg: str | None) -> str:
    """脚注档位：命令行给了就用给的，否则用**源自己登记的默认档**。

    为什么要让源自己定：EPUB 的页下注本来就随文，用 PDF 那套「攒到页末」会把整章
    几万字的注堆成一个大尾巴；而 PDF 的注确实在页末。把默认值写死在命令行里，
    等于逼人每次记住源类型。登记处见 core/epub.py 的 project.defaults。
    """
    if arg:
        return arg
    pj = read_json(wd / "project.json", {}) or {}
    return (pj.get("defaults") or {}).get("footnotes") or "page-end"


# ---------------------------------------------------------------- 子命令

def cmd_probe(args) -> int:
    cfg = load_config()
    out: dict = {"doclab_version": __version__, "config": cfg.public()}
    import importlib.util
    out["pypdf_available"] = importlib.util.find_spec("pypdf") is not None
    if not cfg.token:
        out["api"] = {"ok": False, "error": "未配置 token（MINERU_TOKEN / .env.local）"}
    else:
        from core.mineru_api import MineruClient
        try:
            out["api"] = MineruClient(cfg).probe()
        except Exception as e:
            out["api"] = {"ok": False, "error": str(e)}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        c = out["config"]
        print(f"{BANNER}\n")
        print("【配置】")
        print(f"  API base      : {c['api_base']}")
        print(f"  token         : {'已配置' if c['token_present'] else '未配置'} "
              f"({c['token_hint'] or '—'}, 来源 {c['token_source']})")
        print(f"  分片页数上限  : {c['shard_pages']}")
        print(f"  前置切块方式  : {c['shard_mode']}"
              f"（{'整本上传由 MinerU 按 page_ranges 切' if c['shard_mode']=='mineru' else '本机 pypdf 切好再传'}）")
        print(f"  识别语言      : {c['language']}｜公式 {c['enable_formula']}｜表格 {c['enable_table']}")
        print(f"  MinerU 输出根 : {c['mineru_output_root']}")
        print(f"  pypdf         : {'可用（可自动分片）' if out['pypdf_available'] else '缺失'}")
        a = out["api"]
        print("\n【MinerU API】")
        if a.get("ok"):
            print(f"  ✅ 连通且鉴权通过（code={a.get('code')} msg={a.get('msg')}）")
        else:
            print(f"  ❌ {a.get('error') or a}")
    return 0 if out.get("api", {}).get("ok") else 1


def cmd_sources(args) -> int:
    rows = []
    for p in sorted(WORK_DIR.iterdir()) if WORK_DIR.is_dir() else []:
        if not p.is_dir():
            continue
        pj = read_json(p / "project.json", None)
        if not pj:
            continue
        stages = {f: (p / f).exists() for f in
                  ("project.json", "outline.json", "page_calibration.json",
                   "export_index.json", "verify.json")}
        v = read_json(p / "verify.json", {}) or {}
        rows.append({
            "source_id": p.name,
            "title": pj.get("doc_title", ""),
            "mode": pj.get("mode", ""),
            "shards": len(pj.get("shards", [])),
            "pages": sum(int(s.get("n_pages") or 0) for s in pj.get("shards", [])),
            "stages": stages,
            "verify": v.get("verdict", "—"),
        })
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print(f"{BANNER}\n")
    if not rows:
        print("还没有登记任何源。")
        return 0
    print(f"{'source_id':44s} {'片':>3s} {'页':>5s} {'校验':>6s}  阶段")
    for r in rows:
        stages = "".join("●" if r["stages"][k] else "○" for k in
                         ("project.json", "outline.json", "page_calibration.json",
                          "export_index.json", "verify.json"))
        print(f"{r['source_id']:44s} {r['shards']:>3d} {r['pages']:>5d} "
              f"{r['verify']:>6s}  {stages}")
    print("\n阶段顺序：工程 ●目录 ●页码 ●导出 ●校验")
    return 0


def cmd_adopt(args) -> int:
    from core.ingest import adopt_existing
    cfg = load_config()
    dirs = [Path(d) for d in (args.dirs or [])]
    if args.keyword and not dirs:
        from core.project import discover_mineru_dirs
        dirs = discover_mineru_dirs(cfg.mineru_output_root, args.keyword)
        if not dirs:
            raise SystemExit(f"{cfg.mineru_output_root} 下没有匹配 {args.keyword} 的工程目录")
    pj = adopt_existing(dirs, cfg, source_id=args.source_id, title=args.title,
                        force=args.force, log=_log)
    if args.json:
        print(json.dumps({"source_id": pj["source_id"], "shards": pj["shards"]},
                         ensure_ascii=False, indent=2))
    return 0


def cmd_ingest(args) -> int:
    from core.ingest import ingest_local
    cfg = load_config()
    pj = ingest_local(Path(args.file), cfg, shard_pages=args.shard_pages,
                      force=args.force, log=_log, title=args.title,
                      shard_mode=args.shard_mode)
    if args.json:
        print(json.dumps({"source_id": pj["source_id"], "shards": pj["shards"]},
                         ensure_ascii=False, indent=2))
    return 0


def cmd_resume(args) -> int:
    """续跑已提交的 OCR 批次：只补没就绪的分片，不重跑已就绪的。

    可以按 batch_id、也可以按 source_id 找 —— 状态落在 `_work/*/ingest_state.json`，
    所以批次号不必只活在控制台日志里。两个都不给时，只有一个未完成批次才会命中。
    """
    from core.ingest import resume_ingest
    pj = resume_ingest(args.batch_id or None, load_config(), log=_log,
                       source_id=args.source or None)
    if args.json:
        print(json.dumps({"source_id": pj["source_id"], "shards": pj["shards"]},
                         ensure_ascii=False, indent=2))
    return 0


def cmd_remove(args) -> int:
    """把源移出工作台：产物移入 _trash（可恢复），原 PDF / MinerU 工程不动。"""
    from core.ingest import remove_source
    sid = resolve_source(args.source)
    r = remove_source(sid, _log)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


def cmd_outline(args) -> int:
    from core.outline import build_tree, write_outline, outline_report
    sid = resolve_source(args.source)
    wd, shards, blocks = load_blocks(sid)
    pj = read_json(wd / "project.json", {}) or {}
    # 若已校准过页码，启用 R5：用书内目录补齐 MinerU 漏标的章
    calib = None
    if (wd / "page_calibration.json").exists():
        from core.pagecal import load_calibration
        calib = load_calibration(wd)
    _log(f"[outline] {sid}：{len(shards)} 片 / {len(blocks)} 块"
         f"{'（含目录补章）' if calib else '（未校准，跳过目录补章）'}")
    from core import manual
    _lv = manual.levels_for(wd)
    _dl = manual.deleted_for(wd)
    _ad = manual.added_for(wd)
    if _lv:
        _log(f"[outline] 叠加人工定级 {len(_lv)} 处")
    if _dl:
        _log(f"[outline] 剔除人工删除 {len(_dl)} 条")
    if _ad:
        _log(f"[outline] 并入人工新增标题 {len(_ad)} 条")
    _tree, outline = build_tree(sid, blocks, doc_title=pj.get("doc_title", ""), calib=calib,
                                manual_levels=_lv, manual_deleted=_dl, manual_added=_ad)
    write_outline(wd, outline)
    write_text(wd / "目录索引.md", outline_report(outline))
    _log(f"[outline] 节点 {outline['node_total']} 个 → {wd/'outline.json'}")
    n_fix = sum(1 for a in outline.get("toc_repair", []) if a.get("status") == "已补")
    if outline.get("toc_repair"):
        _log(f"[outline] 目录补章：补入 {n_fix} 个漏标章"
             f"（另有 {len(outline['toc_repair']) - n_fix} 个跳过）")
    if args.json:
        print(json.dumps({
            "source_id": sid, "node_total": outline["node_total"],
            "level_source": outline.get("level_source"),
            "level_stats": outline["level_stats"],
            "toc_crosscheck": outline.get("toc_crosscheck", {}),
            "toc_repair": outline.get("toc_repair", []),
            "path": str(wd / "outline.json"),
        }, ensure_ascii=False, indent=2))
    else:
        print(f"\n{BANNER}\n")
        print(f"层级统计（{sid}）　层级来源："
              f"{'出版方标签 h1..h6' if outline.get('level_source') == 'tag' else '中文编号推断'}")
        print(f"{'层级':>4s} {'节点数':>6s} {'切到本层=文件数':>16s} {'总字数':>10s} {'平均':>8s} {'空节点':>6s}")
        for k in sorted(outline["level_stats"], key=int):
            s = outline["level_stats"][k]
            print(f"{'L'+k:>4s} {s['nodes']:>6d} {s['files_if_split']:>16d} "
                  f"{s['chars']:>10,d} {s['avg_chars']:>8,d} {s['empty']:>6d}")
        cc = outline.get("toc_crosscheck", {})
        if cc.get("available"):
            print(f"\n印刷目录交叉校验：目录 {cc['toc_entry_count']} 条，树里找到 "
                  f"{cc['matched']} 条（目录覆盖率 {cc['toc_coverage']:.0%}）")
            print(f"  本级标题 {cc['heading_count']} 个，其中 {cc['matched']} 个有目录背书"
                  f"（{cc['match_rate']:.0%}；目录通常只列到章，此数偏低属正常）")
        else:
            print(f"\n印刷目录交叉校验：{cc.get('note')}")
        print(f"\n完整目录树见 {wd/'目录索引.md'}")
        print(f"选好深度后：python doclab.py export {sid} --depth N")
    return 0


def cmd_pagecal(args) -> int:
    from core.pagecal import calibrate, write_calibration, calibration_report
    sid = resolve_source(args.source)
    wd, shards, blocks = load_blocks(sid)
    smeta = [s.to_dict() for s in shards]
    _log(f"[pagecal] {sid}：扫描 {len(blocks)} 块取页码观测 …")
    from core import manual
    man = manual.anchors_for(wd)
    if man:
        _log(f"[pagecal] 叠加人工锚点 {sum(len(v) for v in man.values())} 处")
    calib = calibrate(sid, blocks, smeta, anchors=man)
    write_calibration(wd, calib)
    write_text(wd / "页码校准报告.md", calibration_report(calib))
    _log(f"[pagecal] 判定 {calib.verdict}／{calib.locator_type}：{calib.reason}")
    if args.json:
        print(json.dumps({
            "source_id": sid, "verdict": calib.verdict, "locator_type": calib.locator_type,
            "reason": calib.reason, "segments": [s.to_dict() for s in calib.segments],
            "gaps": calib.gaps, "continuity": calib.continuity,
            "path": str(wd / "page_calibration.json"),
        }, ensure_ascii=False, indent=2))
    else:
        print(f"\n{BANNER}\n")
        print(calibration_report(calib))
    return 0


def cmd_export(args) -> int:
    from core.exporter import export
    from core.pagecal import load_calibration
    from core.outline import load_outline
    sid = resolve_source(args.source)
    wd, shards, blocks = load_blocks(sid)
    outline = load_outline(wd)
    calib = load_calibration(wd)
    od = out_dir(sid) / f"L{args.depth}"
    _log(f"[export] {sid} → {od}（深度 L{args.depth}）")
    meta = export(sid, wd, od, blocks, calib, outline, args.depth,
                  copy_images=not args.no_images,
                  footnote_mode=fn_mode(wd, args.footnotes),
                  shards_meta=[s.to_dict() for s in shards])
    write_text(od / "conversion_meta.json", json.dumps(meta, ensure_ascii=False, indent=2))
    _log(f"[export] {meta['stats']['files']} 个文件 / {meta['stats']['chars']:,} 字 / "
         f"{meta['stats']['images_copied']} 张图")
    if meta["stats"].get("stale_removed"):
        _log(f"[export] 清掉 {meta['stats']['stale_removed']} 个上一轮留下的文件"
             f"（这一轮已经不再切出它们了）：{', '.join(meta.get('stale_removed', [])[:6])}"
             + (" …" if meta["stats"]["stale_removed"] > 6 else ""))
    if meta["stats"]["unmapped_pages"]:
        _log(f"[export] ⚠️ {meta['stats']['unmapped_pages']} 处页锚无法映射到纸书页码，"
             f"已标 p=pdf-N unmapped")
    if args.json:
        print(json.dumps({"source_id": sid, "depth": args.depth, "out_dir": str(od),
                          "stats": meta["stats"], "locator_type": meta["locator_type"],
                          "page_offset": meta["page_offset"]},
                         ensure_ascii=False, indent=2))
    return 0


def cmd_audit(args) -> int:
    """目录校核：把「人肉通读几百条目录」压成「复核 N 条待定项」。

    只读 outline / page_calibration / manual_edits，写 `目录校核.json` 与
    `目录校核.md`，**不动任何现有产物**。输出的 findings 里凡涉及「疑似缺标题」
    的都带 probe（搜索窗口），供 Agent 取证后再决定改不改。
    """
    from core.audit import run, audit_report_md
    sid = resolve_source(args.source)
    wd = work_dir(sid)
    _log(f"[audit] {sid}：校核目录结构…")
    rep = run(sid, wd, depth=args.depth)
    _log(f"[audit] 判定 {rep['verdict']}｜🔴 {rep['severity']['high']} "
         f"🟡 {rep['severity']['warn']} ⚪ {rep['severity']['info']}")
    _log(f"[audit] → {wd/'目录校核.json'} / {wd/'目录校核.md'}")
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(f"\n{BANNER}\n")
        print(audit_report_md(rep))
        print(f"\n完整清单见 {wd/'目录校核.md'}")
    return 2 if rep["severity"]["high"] else 0


def cmd_fix(args) -> int:
    """按修订单改人工核定层。默认**只演练**，`--apply` 才落盘。

    修订单是 Agent/人判定的产物，每个条目必须带 evidence（无证据拒收，见
    core/fixplan）。执行后只写 manual_edits.json，然后按需重算 —— 绝不直接改
    outline.json / page_calibration.json。
    """
    from core.fixplan import (load_plan, validate as validate_plan,
                              apply as apply_plan, write_template, PlanError)
    sid = resolve_source(args.source)
    wd = work_dir(sid)
    if args.template:
        p = write_template(Path(args.template))
        _log(f"[fix] 修订单模板 → {p}")
        return 0
    if not args.plan:
        raise SystemExit("要么给 --plan <修订单.json>，要么给 --template <新文件>")
    try:
        plan = load_plan(Path(args.plan))
        items, warns = validate_plan(plan, wd)
    except PlanError as e:
        raise SystemExit(f"❌ 修订单不可用：{e}")
    for w in warns:
        _log(f"[fix] ⚠️ {w}")
    if not args.apply:
        _log(f"[fix] 演练：{len(items)} 条将被应用（加 --apply 才落盘）")
        for it in items:
            keys = {k: v for k, v in it.items()
                    if k not in ("evidence", "_i", "why", "snapshot")}
            _log(f"  {keys}")
            _log(f"      依据：{it.get('evidence', '')[:100]}")
        if args.json:
            print(json.dumps({"dry_run": True, "items": len(items), "warnings": warns,
                              "plan": [{k: v for k, v in it.items() if k != "_i"}
                                       for it in items]}, ensure_ascii=False, indent=2))
        return 0
    rec = apply_plan(plan, wd, sid)
    _log(f"[fix] 应用 {rec['applied']} 条"
         + (f"，失败 {len(rec['failed'])} 条" if rec["failed"] else "")
         + f" → {wd/'manual_edits.json'}")
    for f in rec["failed"]:
        _log(f"[fix] ❌ items[{f['index']}] {f['action']}：{f['error']}")
    if rec["needs_pagecal"]:
        _log("[fix] 锚点动过 → 先重算页码")
        rc = cmd_pagecal(argparse.Namespace(source=sid, json=False))
        if rc:
            _log("[fix] ⚠️ 页码重算未通过，目录重算仍继续（人工锚点优先，见 core/manual）")
    _log("[fix] 重算目录树")
    cmd_outline(argparse.Namespace(source=sid, json=False))
    if args.json:
        print(json.dumps(rec, ensure_ascii=False, indent=2))
    return 0 if not rec["failed"] else 2


def cmd_grep(args) -> int:
    """在正文块里搜关键词 —— 「疑似缺了一条标题」的取证入口。

    为什么要单独有它：MinerU 漏掉一个标题时，那行字在它眼里就是一段**普通正文**，
    目录列表里根本没有它，界面第三栏也没法按标题筛。校核（audit）给出的 probe 只圈
    出「去哪几十个块里找」，真正去读还得能把块摊开按原文搜 —— 这就是那个摊开。
    为无头运行准备，Agent 不必起界面。
    """
    from core.pagecal import load_calibration
    from core.project import NOISE_TYPES
    sid = resolve_source(args.source)
    wd, _shards, blocks = load_blocks(sid)
    try:
        calib = load_calibration(wd)
    except Exception:                                             # noqa: BLE001
        calib = None

    def norm(s: str) -> str:
        """去空白 + 全角数字折半角。

        OCR 常把「第三章」吐成「第 三 章」，或把编号认成全角。归一化后再比，
        否则按原样搜必然搜不到，看着像"正文里确实没有" —— 那是假的。
        """
        out = []
        for ch in str(s or ""):
            if ch.isspace() or ch == "\u3000":
                continue
            if "０" <= ch <= "９":
                out.append(chr(ord(ch) - 0xFEE0))
            else:
                out.append(ch)
        return "".join(out)

    lo = hi = None
    if args.gid:
        m = re.match(r"^\s*(\d+)\s*(?:[-–~,]\s*(\d+))?\s*$", args.gid)
        if not m:
            raise SystemExit("--gid 写法：36 或 36-124")
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) else lo
    needle = norm(args.q) if args.q else ""
    if not needle and lo is None:
        raise SystemExit("至少要给 --q 或 --gid")

    hits = []
    for b in blocks:
        if b.type in NOISE_TYPES:
            continue
        if lo is not None and not (lo <= b.gid <= hi):
            continue
        text = b.text or b.table_body or ""
        if not text.strip():
            text = "；".join(b.caption) if b.caption else ""
        if not text.strip():
            continue
        if needle and needle not in norm(text):
            continue
        loc = calib.locator(b.shard, b.page_idx) if calib else None
        if args.page and str(loc) != str(args.page):
            continue
        hits.append({"gid": b.gid, "shard": b.shard, "page_idx": b.page_idx,
                     "printed": loc, "type": b.type, "is_heading": bool(b.is_heading),
                     "text": text[:400], "full_len": len(text)})

    limit = int(args.limit or 40)
    shown = hits[:limit]
    if args.json:
        print(json.dumps({"source_id": sid, "q": args.q, "gid_range": [lo, hi],
                          "total": len(hits), "rows": shown},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"{sid}：命中 {len(hits)} 块" + (f"，显示前 {len(shown)}" if len(hits) > len(shown) else ""))
    for h in shown:
        print(f"  gid={h['gid']:<6} {h['shard']}p{h['page_idx']:<4} "
              f"{str(h['printed']):<9} {h['type']:<6}"
              f"{'[h]' if h['is_heading'] else '   '} {h['text'][:96]}")
    return 0


def cmd_toc_export(args) -> int:
    """目录结构 → JSON（AI 语义核对的入口）。

    平铺、文档顺序，每条带 key/level/title/gid/页码/字数。level > depth 的条目
    **不导出** —— 「L3 之后暂时忽略」由导出直接保证，导回时也只 diff 这份集合，
    不会把没看见的深层条目误判成删除。
    """
    from core.tocio import export_toc, FILE
    sid = resolve_source(args.source)
    wd = work_dir(sid)
    data = export_toc(wd, sid, args.depth)
    write_json(wd / FILE, data)
    _log(f"[toc-export] {sid}：导出 {data['node_count']} 条（核对深度 ≤L{data['depth_limit']}，"
         f"全书 {data['node_total_all']} 条）→ {wd / FILE}")
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def cmd_toc_import(args) -> int:
    """把 AI 改好的目录 JSON 导回：diff 自动翻译成人工核定层落盘并重算。

    AI 只需要会改 JSON（改 level / 删条 / 加条），翻译规则见 core/tocio：
    条目消失=删除（快照自动补）、level 变=定级、新增=add（必须真实正文块 gid）。
    默认演练，`--apply` 才落盘；任何一条不合法整单拒收。
    """
    from core.tocio import import_toc
    sid = resolve_source(args.source)
    wd = work_dir(sid)
    if not args.file:
        raise SystemExit("要给 --file <改后的 JSON 路径>")
    new = read_json(Path(args.file), None)
    if not isinstance(new, dict):
        raise SystemExit(f"读不出 JSON 对象：{args.file}")
    if args.apply:
        new.setdefault("note", "")
    try:
        r = import_toc(wd, sid, new, do_apply=args.apply)
    except ValueError as e:
        raise SystemExit(f"❌ {e}")
    if not r["ok"]:
        _log(f"[toc-import] ❌ 整单拒收（{len(r['errors'])} 处不合法，一条都不改）：")
        for e in r["errors"]:
            _log(f"        - {e}")
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        return 2
    for w in r["warnings"]:
        _log(f"[toc-import] ⚠️ {w}")
    if not r.get("applied") and not r["item_count"]:
        _log("[toc-import] 无改动 —— 目录与这份 JSON 一致")
    if not args.apply:
        _log(f"[toc-import] 演练：{r['item_count']} 条将被应用（加 --apply 才落盘）")
        for c in r["changes"]:
            _log(f"        {c}")
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    _log(f"[toc-import] 应用 {r.get('applied', 0)} 条"
         + (f"，失败 {len(r.get('failed') or [])} 条" if r.get("failed") else ""))
    for f in r.get("failed") or []:
        _log(f"[toc-import] ❌ {f}")
    _log("[toc-import] 重算目录树")
    rc = cmd_outline(argparse.Namespace(source=sid, json=False))
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    return rc if r.get("failed") else (0 if not r.get("failed") else 2)


def cmd_verify(args) -> int:
    from core.pagecal import load_calibration
    from core.verify import run_all, verify_report_md
    sid = resolve_source(args.source)
    wd, shards, blocks = load_blocks(sid)
    calib = load_calibration(wd)
    # 找最近一次导出目录
    od = out_dir(sid) / f"L{args.depth}"
    if not od.is_dir():
        cands = sorted(p for p in out_dir(sid).glob("L*") if p.is_dir()) if out_dir(sid).is_dir() else []
        if not cands:
            raise SystemExit(f"{out_dir(sid)} 下没有导出结果 —— 先跑 export")
        od = cands[-1]
    _log(f"[verify] {sid} ← {od}")
    rep = run_all(wd, od, blocks, calib)
    write_text(wd / "校验报告.md", verify_report_md(rep, sid))
    write_text(od / "校验报告.md", verify_report_md(rep, sid))
    _log(f"[verify] 总判定：{rep['verdict']}")
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(f"\n{BANNER}\n")
        print(verify_report_md(rep, sid))
    return 0 if rep["verdict"] != "fail" else 2


def cmd_run(args) -> int:
    # 顺序不能反：pagecal 必须早于 outline，否则 build_tree 拿不到校准结果，
    # R5「用书内印刷目录补齐 MinerU 漏标的章」不会触发。
    for fn, sub in ((cmd_pagecal, {"source": args.source, "json": False}),
                    (cmd_outline, {"source": args.source, "json": False}),
                    (cmd_export, {"source": args.source, "depth": args.depth,
                                  "no_images": args.no_images, "footnotes": args.footnotes,
                                  "json": False}),
                    (cmd_verify, {"source": args.source, "depth": args.depth, "json": False})):
        rc = fn(argparse.Namespace(**sub))
        if rc:
            return rc
        print()
    return 0


def cmd_status(args) -> int:
    sid = resolve_source(args.source)
    wd = work_dir(sid)
    pj = read_json(wd / "project.json", {}) or {}
    out: dict = {"source_id": sid, "work_dir": str(wd), "project": pj}
    for f, key in (("outline.json", "outline"), ("page_calibration.json", "page_calibration"),
                   ("verify.json", "verify")):
        out[key] = read_json(wd / f, None)
    out["outputs"] = sorted(str(p) for p in out_dir(sid).glob("L*") if p.is_dir()) \
        if out_dir(sid).is_dir() else []
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"{BANNER}\n")
        print(f"source_id : {sid}")
        print(f"标题      : {pj.get('doc_title')}")
        print(f"来源模式  : {pj.get('mode')}")
        shard_txt = ", ".join(
            f"{s['tag']}({s.get('n_pages')}页)" for s in pj.get("shards", []))
        print(f"分片      : {shard_txt}")
        oc = out.get("outline")
        if oc:
            print(f"目录索引  : {oc['node_total']} 节点；" + "；".join(
                f"L{k}={v['nodes']}" for k, v in sorted(oc["level_stats"].items(), key=lambda x: int(x[0]))))
        pc = out.get("page_calibration")
        if pc:
            print(f"页码校准  : {pc['verdict']}／{pc['locator_type']} —— {pc['reason']}")
        vf = out.get("verify")
        if vf:
            print(f"校验      : {vf['verdict']} " + "｜".join(
                f"{g['id']}={g['status']}" for g in vf["gates"]))
        if out["outputs"]:
            print("导出目录  :")
            for o in out["outputs"]:
                print(f"  {o}")
    return 0


def cmd_sections(args) -> int:
    """列出某深度的分节清单（供 Agent 选择要读哪一节）。"""
    sid = resolve_source(args.source)
    d = out_dir(sid) / f"L{args.depth}"
    if not d.is_dir():
        cands = sorted(p for p in out_dir(sid).glob("L*") if p.is_dir()) if out_dir(sid).is_dir() else []
        if not cands:
            raise SystemExit(f"{out_dir(sid)} 下没有导出结果 —— 先跑 export")
        d = cands[-1]
    meta = read_json(d / "conversion_meta.json", {}) or {}
    rows = [{"seq": o["seq"], "nid": o["nid"], "title": o["title"], "file": o["path"],
             "printed_first": o["printed_first"], "printed_last": o["printed_last"],
             "chars": o["chars"], "flags": o.get("flags", [])} for o in meta.get("outputs", [])]
    if args.json:
        print(json.dumps({"source_id": sid, "out_dir": str(d),
                          "locator_type": meta.get("locator_type"), "sections": rows},
                         ensure_ascii=False, indent=2))
    else:
        print(f"{BANNER}\n\n{d} —— {len(rows)} 节\n")
        for r in rows:
            pg = f"{r['printed_first']}–{r['printed_last']}" if r["printed_first"] else "—"
            print(f"  {r['seq']:>3d}  {r['file']:<58s} p{pg:<12s} {r['chars']:>7,d}字")
    return 0


def cmd_read(args) -> int:
    """按序号/文件名前缀读某一节的正文（Agent 消费入口）。"""
    sid = resolve_source(args.source)
    d = out_dir(sid) / f"L{args.depth}"
    if not d.is_dir():
        cands = sorted(p for p in out_dir(sid).glob("L*") if p.is_dir()) if out_dir(sid).is_dir() else []
        if not cands:
            raise SystemExit("没有导出结果 —— 先跑 export")
        d = cands[-1]
    files = sorted(p for p in d.glob("*.md") if p.name != "00-目录.md")
    target = None
    for p in files:
        if args.section and (p.name == args.section
                             or p.name.startswith(args.section)
                             or p.name.split("_", 1)[0] == args.section.zfill(3)):
            target = p
            break
    if target is None:
        raise SystemExit(f"没找到 {args.section}；用 sections 子命令列清单")
    text = target.read_text(encoding="utf-8")
    if args.page:
        keep, on = [], False
        for line in text.splitlines():
            m = re.match(r"^<!--\s*p=([\w\-]+)", line)
            if m:
                on = (m.group(1) == args.page)
            if on or not line.startswith("<!-- p="):
                keep.append(line)
        text = "\n".join(keep)
    if args.json:
        print(json.dumps({"file": target.name, "path": str(target), "content": text},
                         ensure_ascii=False, indent=2))
    else:
        print(text)
    return 0


def cmd_ui(args) -> int:
    from core.server import serve
    return serve(port=args.port, open_browser=not args.no_browser)


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="doclab", description=BANNER,
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("probe", help="环境与 API 自检")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_probe)

    s = sub.add_parser("sources", help="列出已登记的源")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_sources)

    s = sub.add_parser("adopt", help="登记本机已有的 MinerU 工程目录")
    s.add_argument("--dirs", nargs="*", help="工程目录（可多个）")
    s.add_argument("--keyword", help="在 MinerU 输出根下按关键词自动找")
    s.add_argument("--source-id")
    s.add_argument("--title")
    s.add_argument("--force", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_adopt)

    s = sub.add_parser("ingest", help="入库：PDF 调 MinerU 做 OCR；EPUB 本机解析（不烧配额）")
    s.add_argument("file")
    s.add_argument("--shard-pages", type=int, help="分片页数上限（默认配置值 200；EPUB 不分片）")
    s.add_argument("--shard-mode", choices=("mineru", "local"),
                   help="前置切块：mineru=整本上传按 page_ranges 交给 MinerU 切；"
                        "local=本机 pypdf 先切好再传（默认取配置；EPUB 不分片）")
    s.add_argument("--title")
    s.add_argument("--force", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_ingest)

    s = sub.add_parser("resume", help="续跑没落地的 OCR：只补未就绪的分片")
    s.add_argument("batch_id", nargs="?", help="提交时打印的 batch_id")
    s.add_argument("--source", help="或按 source_id 找（与其同时给则按 batch_id）")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_resume)

    s = sub.add_parser("remove", help="把源移出工作台（产物进 _trash，可恢复；原 PDF 不动）")
    s.add_argument("source", nargs="?")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_remove)

    s = sub.add_parser("outline", help="构建目录索引")
    s.add_argument("source", nargs="?")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_outline)

    s = sub.add_parser("pagecal", help="页码校准")
    s.add_argument("source", nargs="?")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_pagecal)

    s = sub.add_parser("export", help="按层级切分导出")
    s.add_argument("source", nargs="?")
    s.add_argument("-d", "--depth", type=int, required=True, help="切到第几层（0=整本一个文件）")
    s.add_argument("--no-images", action="store_true", help="不搬运图片")
    s.add_argument("--footnotes", choices=("page-end", "inline", "drop"), default=None,
                   help="脚注档位（不给则取源自己的默认：EPUB 随文，PDF 页末）")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("audit", help="目录校核（结构/编号/页码/印刷目录对照，只读不改）")
    s.add_argument("source", nargs="?")
    s.add_argument("-d", "--depth", type=int, default=3,
                   help="关注到第几层（默认 3；更深的问题仍然报，只是降为提示）")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_audit)

    s = sub.add_parser("fix", help="按修订单改人工核定层（默认演练；--apply 才落盘）")
    s.add_argument("source", nargs="?")
    s.add_argument("--plan", help="修订单 JSON（agent/人产出，每条必须带 evidence）")
    s.add_argument("--template", help="写到这个路径，得到一份修订单模板")
    s.add_argument("--apply", action="store_true", help="真的落盘并重算（默认只演练）")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_fix)

    s = sub.add_parser("grep", help="在正文块里搜关键词（找漏掉的标题、拿 gid；无头取证）")
    s.add_argument("source", nargs="?")
    s.add_argument("--q", help="关键词（去空白后包含匹配，全角数字自动折半角）")
    s.add_argument("--gid", help="只在这些块里搜：36 或 36-124")
    s.add_argument("--page", help="只取某个定位符的页，如 57 / front-9")
    s.add_argument("--limit", type=int, default=40)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_grep)

    s = sub.add_parser("toc-export", help="目录结构导出为 JSON（AI 语义核对入口；-d=核对深度）")
    s.add_argument("source", nargs="?")
    s.add_argument("-d", "--depth", type=int, default=3,
                   help="核对深度：level 更深的条目不导出＝核对时忽略（默认 3）")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_toc_export)

    s = sub.add_parser("toc-import", help="把 AI 改好的目录 JSON 导回（默认演练；--apply 落盘并重算）")
    s.add_argument("source", nargs="?")
    s.add_argument("--file", help="AI 修改后的目录 JSON 路径（toc-export 那份改的）")
    s.add_argument("--apply", action="store_true", help="真的落盘并重算（默认只演练）")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_toc_import)

    s = sub.add_parser("verify", help="三道闸门校验")
    s.add_argument("source", nargs="?")
    s.add_argument("-d", "--depth", type=int, default=2)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("run", help="outline→pagecal→export→verify 一次跑完")
    s.add_argument("source", nargs="?")
    s.add_argument("-d", "--depth", type=int, default=2)
    s.add_argument("--no-images", action="store_true")
    s.add_argument("--footnotes", choices=("page-end", "inline", "drop"), default=None,
                   help="脚注档位（不给则取源自己的默认：EPUB 随文，PDF 页末）")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("status", help="查看某源状态")
    s.add_argument("source", nargs="?")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("sections", help="列出某深度的分节清单")
    s.add_argument("source", nargs="?")
    s.add_argument("-d", "--depth", type=int, default=2)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_sections)

    s = sub.add_parser("read", help="读某一节正文（Agent 消费入口）")
    s.add_argument("section", help="序号或文件名前缀")
    s.add_argument("source", nargs="?")
    s.add_argument("-d", "--depth", type=int, default=2)
    s.add_argument("--page", help="只取某个页锚下的内容，如 287")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_read)

    s = sub.add_parser("ui", help="起本地界面")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(func=cmd_ui)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n中断。")
        return 130
    except BrokenPipeError:
        return 0
    except Exception as e:
        print(f"\n❌ {type(e).__name__}: {e}", file=sys.stderr)
        if "--traceback" in (argv or sys.argv):
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
