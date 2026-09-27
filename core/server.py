"""本地界面后端：纯标准库 http.server，零依赖。

界面只做三件事，其余全部复用 core：
  1. 让你看清目录树与各层节点数 → 选切分深度
  2. 让你看清页码校准的依据区间 → 人工确认
  3. 触发 ingest / outline / export / verify，并把日志实时吐出来

长任务（ingest / run）走后台 job，前端轮询进度，避免请求挂死。
"""
from __future__ import annotations

import json
import mimetypes
import os
import threading
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .config import (OUT_DIR, UI_DIR, WORK_DIR, load_config, list_sources,
                     out_dir, resolve_source, save_env_token, work_dir)
from .util import now_iso, read_json

JOBS: dict[str, dict] = {}
_JOB_LOCK = threading.Lock()

# ---------------------------------------------------------------- 能力自述
# 前端每次打开都会拿 bootstrap 里的这份清单自检：它要用到的接口，后端有没有。
#
# 为什么必须有它：`ui/index.html` 是**每次请求现从磁盘读**的，而 Python 代码
# 只在进程启动时 import 一次。于是「改了后端、没重启黑窗」这件事的表现是
# **界面是新的、后端是旧的** —— 新界面去调不存在的接口，后端如实回
# 404 unknown path。使用者看到的是一句没头没尾的「unknown path」，而不是
# 「你这个服务还是上次那个进程」。加接口时把名字补进来，前端就知道该要什么。
API_CAPS = ("edit", "edit_add", "blocks", "epub", "config_token")


def _code_stamp() -> str:
    """后端代码指纹：core/*.py ＋ doclab.py 的 (名字, 大小, mtime) 摘要。

    只用于**核对**：黑窗启动时打印一份、界面日志里也显示一份，两边不一致就说明
    界面在跟另一个进程说话。真正的版本判断走 API_CAPS —— 前端知道自己缺什么，
    不需要两份代码各自维护一个必须手工同步的版本号（那种号必然忘记改）。
    """
    import hashlib

    h = hashlib.md5()
    files = sorted(Path(__file__).parent.glob("*.py"))
    entry = Path(__file__).resolve().parent.parent / "doclab.py"
    if entry.is_file():
        files.append(entry)
    for f in files:
        try:
            st = f.stat()
        except OSError:                             # 文件刚被换掉/删掉，跳过
            continue
        h.update(f"{f.name}:{st.st_size}:{int(st.st_mtime)}\n".encode())
    return h.hexdigest()[:10]


# ---------------------------------------------------------------- 未完成的 ingest

def _slim_shards(shards: list) -> list[dict]:
    return [{
        "tag": s.get("tag", "?"),
        "state": s.get("state", "pending"),
        "err": s.get("err", ""),
        "pages": s.get("pages"),
        "blocks": s.get("blocks"),
        "page_ranges": s.get("page_ranges", ""),
        "name": s.get("name", ""),
        "project_dir": s.get("project_dir", ""),
    } for s in shards]


def list_pending() -> list[dict]:
    """列出「跑到一半的 OCR」——有 ingest_state.json 但还没成源的那些。

    这类目录**没有 project.json**，`list_sources()` 看不见它。所以中断的批次
    在界面上等于消失，用户只能去翻 `_work/`。前置环节要「界面化增强鲁棒性」，
    第一件事就是让这些半成品可见、可续跑、可放弃。
    """
    rows = []
    if not WORK_DIR.is_dir():
        return rows
    for p in sorted(WORK_DIR.iterdir()):
        if not p.is_dir():
            continue
        st = read_json(p / "ingest_state.json", None)
        if not st or not st.get("shards"):
            continue
        sh = _slim_shards(st["shards"])
        rows.append({
            "source_id": p.name,
            "title": st.get("doc_title") or p.name,
            "source_file": st.get("source_file", ""),
            "shard_mode": st.get("shard_mode", ""),
            "shard_pages": st.get("shard_pages"),
            "submitted_at": st.get("submitted_at", ""),
            "shards": sh,
            "done": sum(1 for s in sh if s["state"] == "unzipped"),
            "total": len(sh),
            "registered": (p / "project.json").exists(),
        })
    return rows


# ---------------------------------------------------------------- job

def start_job(kind: str, fn) -> dict:
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "kind": kind, "status": "running", "log": [],
           "result": None, "error": None, "started_at": now_iso()}
    with _JOB_LOCK:
        JOBS[jid] = job

    def log(msg: str) -> None:
        job["log"].append(msg)
        if len(job["log"]) > 4000:
            del job["log"][:1000]

    def run():
        try:
            job["result"] = fn(log)
            job["status"] = "done"
        except Exception as e:                      # noqa: BLE001
            job["error"] = f"{type(e).__name__}: {e}"
            job["log"].append("❌ " + job["error"])
            job["log"].append(traceback.format_exc()[-1500:])
            job["status"] = "error"
        finally:
            job["ended_at"] = now_iso()

    threading.Thread(target=run, daemon=True).start()
    return job


# ---------------------------------------------------------------- 业务动作

def _load_blocks(sid: str):
    from .project import iter_blocks, load_shards
    wd = work_dir(sid)
    shards = load_shards(wd)
    return wd, shards, iter_blocks(shards)


def act_pagecal(sid: str, log):
    from . import manual
    from .pagecal import calibration_report, calibrate, write_calibration
    from .util import write_text
    wd, shards, blocks = _load_blocks(sid)
    log(f"[pagecal] 扫描 {len(blocks)} 块取页码观测 …")
    man = manual.anchors_for(wd)
    if man:
        log(f"[pagecal] 叠加人工锚点 {sum(len(v) for v in man.values())} 处")
    calib = calibrate(sid, blocks, [s.to_dict() for s in shards], anchors=man)
    write_calibration(wd, calib)
    write_text(wd / "页码校准报告.md", calibration_report(calib))
    log(f"[pagecal] {calib.verdict}／{calib.locator_type}：{calib.reason}")
    return calib.to_dict()


def act_outline(sid: str, log):
    from . import manual
    from .outline import build_tree, outline_report, write_outline
    from .util import write_text
    wd, shards, blocks = _load_blocks(sid)
    pj = read_json(wd / "project.json", {}) or {}
    calib = None
    if (wd / "page_calibration.json").exists():
        from .pagecal import load_calibration
        calib = load_calibration(wd)
    lv = manual.levels_for(wd)
    dl = manual.deleted_for(wd)
    ad = manual.added_for(wd)
    log(f"[outline] {len(blocks)} 块{'（含目录补章）' if calib else '（未校准）'}"
        + (f"，叠加人工定级 {len(lv)} 处" if lv else "")
        + (f"，剔除人工删除 {len(dl)} 条" if dl else "")
        + (f"，并入人工新增标题 {len(ad)} 条" if ad else ""))
    _t, outline = build_tree(sid, blocks, doc_title=pj.get("doc_title", ""), calib=calib,
                             manual_levels=lv, manual_deleted=dl, manual_added=ad)
    write_outline(wd, outline)
    write_text(wd / "目录索引.md", outline_report(outline))
    n_fix = sum(1 for a in outline.get("toc_repair", []) if a.get("status") == "已补")
    n_add = outline.get("manual_added_count", 0)
    log(f"[outline] {outline['node_total']} 节点"
        + (f"；目录补章 {n_fix} 个" if outline.get("toc_repair") else "")
        + (f"；人工新增标题 {n_add} 条" if n_add else ""))
    return {"node_total": outline["node_total"], "level_stats": outline["level_stats"],
            "toc_crosscheck": outline.get("toc_crosscheck", {}),
            "toc_repair": outline.get("toc_repair", [])}


def act_apply(sid: str, log):
    """把人工核定落进产物：重跑 pagecal + outline，但不导出。

    导出单独一步 —— 人还在改层级和页码时不该反复写 out/。
    """
    log(f"[apply] {sid}：按人工核定重算页码与目录")
    act_pagecal(sid, log)
    res = act_outline(sid, log)
    from . import manual
    res["manual"] = manual.summary(work_dir(sid))
    return res


def _row_key(n: dict) -> str:
    """一条目录行的稳定主键（= outline.Node.key）。

    老 outline.json 没有 key 字段，回落到 str(gid_start) —— 与历史 manual_edits
    的纯数字键逐字一致，所以老数据不需要迁移。
    ⚠️ 树里存的是 `gid_start`（不是 `gid`）：读错字段会让所有 key 变成空串，
    界面上就是「所有条目共用一把空钥匙」—— 一模一样的旧病。
    """
    k = n.get("key")
    if k:
        return str(k)
    g = n.get("gid_start")
    return "" if g is None else str(g)


def _row_pos(key: str, gid) -> tuple:
    """目录行的文档序位置：先 gid，同一 gid 上**真实块在借 gid 的节点之前**。

    与 outline.doc_order 同一条规则（这里不能 import，server 是延迟依赖 outline 的）。
    借 gid 的节点有两类：目录补章（toc:）与人工新增（add:）。它们的 gid 都是借的，
    必然与那个真实块同号，插回原位时必须有一个确定的先后。
    """
    k = str(key or "")
    borrowed = 1 if (k.startswith("toc:") or k.startswith("add:")) else 0
    return (gid if gid is not None else 0, borrowed)


def _flatten_tree(nodes: list, out: list) -> list:
    for n in nodes:
        kids = n.get("children") or []
        # own_chars ＝ 这条标题**自己名下**的正文，不含后代。
        # n_chars 是含后代累计的（父节点包住整棵子树），拿它去凑「到下一个
        # 同级标题之间的字数」会把子树重复加一遍。界面要在改级当场按**新**层级
        # 重算这个区间，就需要这个不含后代的基数。
        own = n.get("n_chars", 0) - sum(c.get("n_chars", 0) for c in kids)
        out.append({
            "key": _row_key(n),
            "gid": n.get("gid_start"), "nid": n.get("nid"), "level": n.get("level", 1),
            "title": n.get("title", ""), "marker": n.get("marker", ""),
            "shard": n.get("shard", ""), "page_idx": n.get("page_idx", 0),
            "chars": n.get("n_chars", 0), "blocks": n.get("n_blocks", 0),
            "own_chars": max(0, own),
            "flags": n.get("flags", []),
        })
        _flatten_tree(kids, out)
    return out


def _deleted_row(key: str, rec: dict) -> dict:
    own = rec.get("own_chars")
    if own is None:
        own = rec.get("chars", 0)   # 老记录没存过 own_chars，退化成含后代的值
    gid = rec.get("gid")
    if gid is None:
        gid = int(key) if str(key).isdigit() else None
    return {"key": key, "gid": gid, "nid": "", "level": rec.get("level", 0),
            "title": rec.get("title") or f"（条目 {key}）",
            "marker": rec.get("marker", ""), "shard": rec.get("shard", ""),
            "page_idx": rec.get("page_idx", 0), "chars": rec.get("chars", 0),
            "own_chars": own, "blocks": rec.get("blocks", 0),
            "flags": [], "deleted": True}


def _pending_added_row(key: str, gid, rec: dict) -> dict:
    """人工新增、但**还没重算进树**的标题行。

    人刚在正文块上点「设为标题」时，outline.json 里当然还没有这个节点（要等
    「应用并重算」才建出来）。界面上必须立刻看得见它、能改级能撤销 ——
    否则他点了没反应，只能靠记忆相信它已经记下了。
    """
    return {"key": key, "gid": gid, "nid": "", "level": rec.get("level", 1),
            "title": rec.get("title", ""), "marker": "chapter",
            "shard": rec.get("shard", ""), "page_idx": rec.get("page_idx", 0),
            "chars": 0, "own_chars": 0, "blocks": 0,
            "flags": ["manual"], "deleted": False, "pending_add": True,
            "offset": rec.get("offset", 0)}


def list_blocks(sid: str, q: str = "", offset: int = 0, limit: int = 80) -> dict:
    """列出正文块，供人在界面上挑「漏掉的标题」落在哪一块。

    为什么要有这个视图：MinerU 漏掉一个标题时，那行文字在它眼里就是一段**普通
    正文**，所以人没法从目录列表里找到它 —— 只能把段落摊开、按正文内容搜。
    搜索是全文包含匹配（不区分大小写），因为人手上拿的正是正文里那一句原文。
    """
    from . import manual
    from .pagecal import load_calibration
    from .project import NOISE_TYPES

    wd, _shards, blocks = _load_blocks(sid)
    oc = read_json(wd / "outline.json", {}) or {}
    ed = manual.load(wd)

    n_level: dict = {}

    def walk(ns):
        for n in ns:
            n_level[n.get("gid_start")] = n.get("level", 1)
            walk(n.get("children") or [])

    walk(oc.get("tree") or [])
    try:
        calib = load_calibration(wd)
    except Exception:                                        # noqa: BLE001
        calib = None

    ql = (q or "").strip().lower()
    hits = []
    for b in blocks:
        if b.type in NOISE_TYPES:
            continue
        text = b.text or b.table_body or ""
        if not text.strip():
            text = "；".join(b.caption) if b.caption else ""
            if not text.strip():
                continue
        if ql and ql not in text.lower():
            continue
        hits.append((b, text))

    total = len(hits)
    offset = max(0, int(offset or 0))
    limit = max(1, min(400, int(limit or 80)))
    rows = []
    for b, text in hits[offset: offset + limit]:
        printed = None
        if calib is not None:
            try:
                printed = calib.locator(b.shard, b.page_idx)
            except Exception:                                # noqa: BLE001
                printed = None
        gk = str(b.gid)
        rows.append({
            "gid": b.gid, "shard": b.shard, "page_idx": b.page_idx,
            "printed": printed, "type": b.type,
            "text": text[:220], "full_len": len(text),
            "is_heading": bool(b.is_heading),
            "in_tree": b.gid in n_level,
            "level": n_level.get(b.gid),
            "added": gk in ed["added"],
            "deleted": f"add:{gk}" in ed["deleted"] or gk in ed["deleted"],
        })
    return {"source_id": sid, "q": q, "offset": offset, "limit": limit,
            "total": total, "rows": rows}


def edit_view(sid: str) -> dict:
    """校对台取数：扁平目录（供逐条定级）+ 页码对照料。

    刻意**不**在后端把「逐页对照」算好：600 行的表，人每改一格都要往返一次后端
    才能看到后面的递加效果，那是没法用的。这里只给分片页数、自动分段和人工锚点，
    递加在前端按同一套 offset 规则实时预览。
    """
    from . import manual
    wd = work_dir(sid)
    oc = read_json(wd / "outline.json", None)
    if not oc:
        raise FileNotFoundError("还没有目录索引 —— 先跑一次 outline")
    pc = read_json(wd / "page_calibration.json", None) or {}
    pj = read_json(wd / "project.json", {}) or {}
    ed = manual.load(wd)

    nodes = _flatten_tree(oc.get("tree") or [], [])
    # 被人工删掉的条目已经不在树里了，界面就没处去拿它的标题。
    # 这里按 key 把它插回原位、标 deleted —— 前端不必自己维护一份影子列表，
    # 每次渲染都直接来自服务端真相，「恢复」才有东西可恢复。
    #
    # 注意「还没点应用」的那个窗口：outline.json 是**上一次重算**的产物，
    # 里面还留着这个节点，直接合并就会出现两条同名行（一条正常、一条已删）。
    # 所以人工删除表优先：树里同 key 的那份先剔掉，再按文档序把已删行插回。
    #
    # 排序按 (gid, 真实块优先)：合成节点的 gid 是借来的，与真实块同号，
    # 用键的字符串序会把它排到 "19" 和 "20" 之间的奇怪位置。
    gone = ed["deleted"]
    added = ed["added"]
    gone_keys = {str(k) for k in gone}
    if gone_keys:
        nodes = [n for n in nodes if _row_key(n) not in gone_keys]
    have = {_row_key(n) for n in nodes}
    # 还没重算进树的待定行：已删条目 + 刚加、还没点「应用并重算」的新增断点。
    # 两者都要插回列表 —— 否则人刚加的标题在界面上看不见，会以为没成功。
    pend: list[tuple] = []
    for k, v in gone.items():
        pend.append((_row_pos(str(k), v.get("gid")), _deleted_row(str(k), v)))
    for k, v in added.items():
        key = f"{manual.ADD_PREFIX}{k}"
        if key in have or key in gone_keys:
            continue
        gid = int(k) if str(k).isdigit() else None
        pend.append((_row_pos(key, gid), _pending_added_row(key, gid, v)))
    if pend:
        pend.sort(key=lambda t: t[0])
        merged, i = [], 0
        for n in nodes:
            # ⚠️ _flatten_tree 产出的是 "gid"（不是树里的 gid_start）。
            # 早先这里写 n.get("gid_start")，恒为 None，于是每一行的位置都算成
            # (0, 0)，待定行被统统排到列表最末尾 —— 位置全错。别再改回去。
            p = _row_pos(_row_key(n), n.get("gid"))
            while i < len(pend) and pend[i][0] < p:
                merged.append(pend[i][1])
                i += 1
            merged.append(n)
        for j in range(i, len(pend)):
            merged.append(pend[j][1])
        nodes = merged
    segs: dict = {}
    for s in pc.get("segments", []):
        segs.setdefault(s["shard"], []).append({
            "start": s["page_idx_start"], "end": s["page_idx_end"],
            "printed_start": s["printed_start"], "printed_end": s["printed_end"],
            "offset": s["offset"], "kind": s.get("kind", "body"),
            "confidence": s.get("confidence", ""), "n_obs": s.get("n_obs", 0),
        })

    # 「PDF 第几页」要给全书连续页号，不能给分片内的 0-based 序号 ——
    # 景晔手上是一个完整的 515 页 PDF，他翻到第 300 页看到的是印刷页码 288，
    # 界面上必须能这样对上，否则每次都要自己加分片偏移。
    shard_pages = pc.get("shard_pages", {}) or {}
    offsets, run = {}, 0
    for tag in pc.get("shards", []) or list(shard_pages):
        offsets[tag] = run
        run += int(shard_pages.get(tag) or 0)

    # 机器真正"看到"过页码的页 —— 让推算出来的页一眼可辨，别让人以为都是识别结果
    obs: dict = {}
    for o in pc.get("observations", []) or []:
        obs.setdefault(o["shard"], {})[str(o["page_idx"])] = o["printed"]

    return {
        "source_id": sid,
        "doc_title": oc.get("doc_title", ""),
        "nodes": nodes,
        "node_total": oc.get("node_total", len(nodes)),
        "levels": ed["levels"],
        "anchors": ed["anchors"],
        "added": ed["added"],
        "manual": manual.summary(wd),
        # 源自己的脚注默认档（EPUB 登记为 inline）。界面拿它预选下拉框 ——
        # 后端不做「用户没选就替他决定」的猜测，只把源自己声明过的口径递上去。
        "footnote_default": (pj.get("defaults") or {}).get("footnotes") or "page-end",
        "source_mode": pj.get("mode", ""),
        "shard_pages": shard_pages,
        "shard_order": pc.get("shards", []) or list(shard_pages),
        "shard_offsets": offsets,
        "observations": obs,
        "auto_segments": segs,
        "calib": {"verdict": pc.get("verdict", ""),
                  "locator_type": pc.get("locator_type", ""),
                  "reason": pc.get("reason", "")},
        "gaps": pc.get("gaps", []),
        "level_stats": oc.get("level_stats", {}),
    }


def act_export(sid: str, depth: int, footnotes: str, no_images: bool, log):
    from .exporter import export
    from .outline import load_outline
    from .pagecal import load_calibration
    wd, shards, blocks = _load_blocks(sid)
    outline = load_outline(wd)
    calib = load_calibration(wd)
    # 前端没给档位时退到源自己登记的那档；都没有才是 PDF 的老口径「页末」。
    # 与 CLI 的 doclab.fn_mode 是同一套优先级，两端不许各写一份不同的默认值。
    if not footnotes:
        pj = read_json(wd / "project.json", {}) or {}
        footnotes = (pj.get("defaults") or {}).get("footnotes") or "page-end"
    od = out_dir(sid) / f"L{depth}"
    log(f"[export] → {od}（脚注：{footnotes}）")
    meta = export(sid, wd, od, blocks, calib, outline, depth,
                  copy_images=not no_images, footnote_mode=footnotes,
                  shards_meta=[s.to_dict() for s in shards])
    log(f"[export] {meta['stats']['files']} 个文件 / {meta['stats']['chars']:,} 字 / "
        f"{meta['stats']['images_copied']} 张图")
    if meta["stats"].get("stale_removed"):
        log(f"[export] 清掉 {meta['stats']['stale_removed']} 个上一轮留下的文件"
            f"（这一轮已经不再切出它们了）：{', '.join(meta.get('stale_removed', [])[:6])}"
            + (" …" if meta["stats"]["stale_removed"] > 6 else ""))
    return {"out_dir": str(od), "stats": meta["stats"],
            "locator_type": meta["locator_type"], "page_offset": meta["page_offset"]}


def act_verify(sid: str, depth: int, log):
    from .pagecal import load_calibration
    from .verify import run_all, verify_report_md
    from .util import write_text
    wd, shards, blocks = _load_blocks(sid)
    calib = load_calibration(wd)
    od = out_dir(sid) / f"L{depth}"
    if not od.is_dir():
        cands = sorted(p for p in out_dir(sid).glob("L*") if p.is_dir())
        if not cands:
            raise FileNotFoundError("还没有导出结果，先跑 export")
        od = cands[-1]
    rep = run_all(wd, od, blocks, calib)
    write_text(wd / "校验报告.md", verify_report_md(rep, sid))
    write_text(od / "校验报告.md", verify_report_md(rep, sid))
    log(f"[verify] {rep['verdict']}")
    return {**rep, "out_dir": str(od)}


def act_ingest(path: str, title: str, shard_pages, shard_mode: str, force: bool, log):
    from .ingest import ingest_local
    pj = ingest_local(Path(path), load_config(), shard_pages=shard_pages,
                      force=bool(force), log=log, title=title or None,
                      shard_mode=shard_mode or None)
    return {"source_id": pj["source_id"], "shards": pj["shards"]}


def act_resume(sid: str, log):
    """续跑一个没落地的 ingest —— 只补没就绪的分片，已 unzipped 的不重做。"""
    from .ingest import resume_ingest
    log(f"[resume] {sid}：按 ingest_state.json 找回断点")
    pj = resume_ingest(cfg=load_config(), log=log, source_id=sid)
    return {"source_id": pj["source_id"], "shards": pj["shards"]}


def act_delete(sid: str, log) -> dict:
    """把一个源移出工作台。

    实现在 core.ingest.remove_source：产物移入 `_trash`（可恢复），
    原始 PDF 与 MinerU 本机工程目录一律不碰。CLI 的 remove 走同一个函数。
    """
    from .ingest import remove_source
    return remove_source(sid, log)


def act_adopt(dirs: list[str], keyword: str, source_id: str, title: str, log):
    from .ingest import adopt_existing
    from .project import discover_mineru_dirs
    cfg = load_config()
    d = [Path(x) for x in (dirs or [])]
    if keyword and not d:
        d = discover_mineru_dirs(cfg.mineru_output_root, keyword)
        if not d:
            raise FileNotFoundError(f"{cfg.mineru_output_root} 下没有匹配 {keyword} 的工程")
    pj = adopt_existing(d, cfg, source_id=source_id or None, title=title or None,
                        force=True, log=log)
    return {"source_id": pj["source_id"], "shards": pj["shards"]}


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "doclab-ui"

    def log_message(self, fmt, *args):            # 静音默认访问日志
        pass

    # -------------------------------------------------- 工具

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))

    def _err(self, msg: str, code: int = 400):
        self._json({"ok": False, "error": msg}, code)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    # -------------------------------------------------- 路由

    def do_GET(self):
        u = urlsplit(self.path)
        q = parse_qs(u.query)
        p = u.path
        try:
            if p in ("/", "/index.html"):
                f = UI_DIR / "index.html"
                self._send(200, f.read_bytes(), "text/html; charset=utf-8")
            elif p == "/api/bootstrap":
                self._json(self.bootstrap())
            elif p == "/api/mineru_dirs":
                # 列出本机 MinerU 输出根下的全部工程，供界面直接挑书登记。
                # 没有这个入口就得凭记忆书名字符串去搜 —— 对「要处理很多本书」是致命的。
                self._json(self.mineru_dirs(q.get("q", [""])[0]))
            elif p == "/api/source":
                self._json(self.source_detail(q.get("id", [""])[0]))
            elif p == "/api/edit":
                self._json({"ok": True,
                            **edit_view(resolve_source(q.get("source", [""])[0]))})
            elif p == "/api/blocks":
                # 正文块视图：漏掉的标题在 MinerU 眼里就是普通正文，只能摊开段落、
                # 按内容搜才找得到。分页返回，别一次把 4000 块全推给浏览器。
                self._json({"ok": True, **list_blocks(
                    resolve_source(q.get("source", [""])[0]),
                    q=q.get("q", [""])[0],
                    offset=q.get("offset", ["0"])[0],
                    limit=q.get("limit", ["80"])[0])})
            elif p == "/api/job":
                jid = q.get("id", [""])[0]
                with _JOB_LOCK:
                    job = JOBS.get(jid)
                self._json(job or {"error": "job not found"}, 200 if job else 404)
            elif p == "/api/file":
                self.serve_file(q.get("path", [""])[0])
            elif p == "/api/download":
                self.serve_zip(q.get("id", [""])[0], q.get("depth", ["2"])[0])
            else:
                self._err("unknown path", 404)
        except Exception as e:                      # noqa: BLE001
            self._err(f"{type(e).__name__}: {e}", 500)

    def do_POST(self):
        u = urlsplit(self.path)
        b = self._body()
        try:
            if u.path == "/api/pagecal":
                sid = resolve_source(b.get("source"))
                self._json({"ok": True, "job": start_job("pagecal",
                            lambda log: act_pagecal(sid, log))["id"]})
            elif u.path == "/api/outline":
                sid = resolve_source(b.get("source"))
                self._json({"ok": True, "job": start_job("outline",
                            lambda log: act_outline(sid, log))["id"]})
            elif u.path == "/api/export":
                sid = resolve_source(b.get("source"))
                depth = int(b.get("depth", 2))
                self._json({"ok": True, "job": start_job("export",
                            lambda log: act_export(sid, depth, b.get("footnotes", "page-end"),
                                                   bool(b.get("no_images")), log))["id"]})
            elif u.path == "/api/verify":
                sid = resolve_source(b.get("source"))
                depth = int(b.get("depth", 2))
                self._json({"ok": True, "job": start_job("verify",
                            lambda log: act_verify(sid, depth, log))["id"]})
            elif u.path == "/api/ingest":
                self._json({"ok": True, "job": start_job("ingest",
                            lambda log: act_ingest(b.get("file", ""), b.get("title", ""),
                                                   b.get("shard_pages"),
                                                   b.get("shard_mode", ""),
                                                   b.get("force", False), log))["id"]})
            elif u.path == "/api/resume":
                sid = str(b.get("source") or "")
                self._json({"ok": True, "job": start_job("resume",
                            lambda log: act_resume(sid, log))["id"]})
            elif u.path == "/api/delete":
                sid = str(b.get("source") or "")
                self._json({"ok": True, "job": start_job("delete",
                            lambda log: act_delete(sid, log))["id"]})
            elif u.path == "/api/adopt":
                self._json({"ok": True, "job": start_job("adopt",
                            lambda log: act_adopt(b.get("dirs", []), b.get("keyword", ""),
                                                  b.get("source_id", ""), b.get("title", ""),
                                                  log))["id"]})
            # ---------------------------------------------- 人工核定（同步，只写小文件）
            elif u.path.startswith("/api/edit"):
                self._json({"ok": True, **self._edit(b.get("source"), u.path, b)})
            elif u.path == "/api/apply":
                sid = resolve_source(b.get("source"))
                self._json({"ok": True, "job": start_job("apply",
                            lambda log: act_apply(sid, log))["id"]})
            # ---------------------------------------------- 配置（同步，只写小文件）
            elif u.path == "/api/config/token":
                # MinerU token 的写入口：落进 .env.local（gitignore 里，永不入库）。
                # 空串 = 清除。环境变量 MINERU_TOKEN 优先级更高，存在时要在
                # 界面上明说「这里改了也不生效」，别让人以为存上了。
                save_env_token(str(b.get("token", "")))
                cfg = load_config()
                self._json({"ok": True, "config": cfg.public(),
                            "env_overrides": bool(os.environ.get("MINERU_TOKEN", "").strip())})
            else:
                self._err("unknown path", 404)
        except Exception as e:                      # noqa: BLE001
            self._err(f"{type(e).__name__}: {e}", 500)

    def _edit(self, source, path: str, b: dict) -> dict:
        """人工校对的写入口。

        全部是同步的 —— 只是往 manual_edits.json 里写一个键值，没有重算，
        界面点一下就立刻有反馈。真正贵的重算放在 /api/apply。
        """
        from . import manual
        sid = resolve_source(source)
        wd = work_dir(sid)
        act = path.rsplit("/", 1)[-1]
        # 主键优先取 key：真实块的 key 就是 str(gid)，两者等价；
        # 目录补出来的合成节点只有 key（它的 gid 是借来的，用 gid 会改到同页那条真实块）。
        k = b.get("key") or b.get("gid")

        if act == "levels" and isinstance(b.get("pairs"), dict):
            manual.set_levels(wd, b["pairs"])
        elif act == "level":
            manual.set_level(wd, k, b["level"])
        elif act == "levels-clear":
            manual.clear_levels(wd)
        elif act == "level-clear":
            manual.clear_level(wd, k)
        elif act == "anchor":
            manual.set_anchor(wd, b["shard"], b["page_idx"], b["printed"])
        elif act == "anchor-clear":
            manual.clear_anchor(wd, b["shard"], b["page_idx"])
        elif act == "anchors-clear":
            manual.clear_anchors(wd, b.get("shard") or None)
        elif act == "delete":
            # 快照带上 gid：已删行要靠它插回目录列表的正确位置
            snap = dict(b.get("node") or b)
            if snap.get("gid") is None:
                snap["gid"] = b.get("gid")
            manual.set_deleted(wd, k, snap)
        elif act == "delete-clear":
            manual.clear_deleted(wd, k)
        elif act == "deletes-clear":
            manual.clear_deleted_all(wd)
        elif act == "add":
            manual.set_added(wd, b["gid"], b.get("title", ""), b.get("level", 2),
                             b.get("offset", 0))
        elif act == "add-clear":
            manual.clear_added(wd, b.get("gid") or k)
        elif act == "reset":
            manual.clear_all(wd)
        else:
            raise ValueError(f"不认识的人工核定操作：{act}")
        return {"source_id": sid, "manual": manual.summary(wd)}

    # -------------------------------------------------- 数据聚合

    def bootstrap(self) -> dict:
        cfg = load_config()
        rows = []
        for sid in list_sources():
            wd = work_dir(sid)
            pj = read_json(wd / "project.json", {}) or {}
            oc = read_json(wd / "outline.json", {}) or {}
            vf = read_json(wd / "verify.json", {}) or {}
            pc = read_json(wd / "page_calibration.json", {}) or {}
            rows.append({
                "source_id": sid,
                "title": pj.get("doc_title") or sid,
                "mode": pj.get("mode", ""),
                "shards": [{"tag": s["tag"], "n_pages": s.get("n_pages")} for s in pj.get("shards", [])],
                "pages": sum(int(s.get("n_pages") or 0) for s in pj.get("shards", [])),
                "has": {"outline": bool(oc), "pagecal": bool(pc),
                        "export": out_dir(sid).is_dir() and any(out_dir(sid).glob("L*")),
                        "verify": bool(vf)},
                "verify": vf.get("verdict", ""),
                "locator_type": pc.get("locator_type", ""),
                "node_total": oc.get("node_total", 0),
                "levels": {k: v["nodes"] for k, v in (oc.get("level_stats") or {}).items()},
            })
        return {"ok": True, "config": cfg.public(), "sources": rows,
                "pending": list_pending(),
                "mineru_root": str(cfg.mineru_output_root),
                # 版本自检用：前端拿 caps 比对自己需要的能力，拿 code_stamp 显示
                "caps": list(API_CAPS),
                "code_stamp": _code_stamp(),
                "pid": os.getpid()}

    def mineru_dirs(self, q: str = "") -> dict:
        """本机 MinerU 输出根下的工程清单（含页数），并标出哪些已登记为源。"""
        from .project import discover_mineru_dirs, load_project_dir

        cfg = load_config()
        root = cfg.mineru_output_root
        dirs = discover_mineru_dirs(root, q or "")
        # 已登记判定必须按 shard.project_dir 的目录名精确比对。
        # 早先用「源 id 前缀匹配目录名」是错的：目录名常带「作者 - 年份 - 」
        # 前缀（如「铁钟 - 2019 - 文化遗产信息模型的虚拟修复研究.pdf-<uuid>」），
        # 而源 id 是书名，startswith 永远为假 —— 已跑过的书会被标成未登记，
        # 用户再登记一次就是重复源。
        registered = {Path(s["project_dir"]).name
                      for sid in list_sources()
                      for s in (read_json(work_dir(sid) / "project.json", {}) or {}).get("shards", [])
                      if s.get("project_dir")}
        rows = []
        for d in dirs:
            try:
                items, _ = load_project_dir(d)
                pages = max((int(x.get("page_idx", 0)) + 1 for x in items), default=0)
            except Exception:                              # noqa: BLE001
                pages = 0
                items = []
            rows.append({
                "dir": str(d),
                "name": d.name,
                "pages": pages,
                "blocks": len(items),
                "registered": d.name in registered,
            })
        rows.sort(key=lambda r: -r["pages"])
        return {"ok": True, "root": str(root), "count": len(rows), "dirs": rows}

    def source_detail(self, arg: str) -> dict:
        sid = resolve_source(arg)
        wd = work_dir(sid)
        proj = read_json(wd / "project.json", {}) or {}
        outline = read_json(wd / "outline.json", {}) or {}
        calib = read_json(wd / "page_calibration.json", {}) or {}
        verify = read_json(wd / "verify.json", {}) or {}
        exports = []
        for d in sorted(out_dir(sid).glob("L*")) if out_dir(sid).is_dir() else []:
            if d.is_dir():
                meta = read_json(d / "conversion_meta.json", {}) or {}
                exports.append({"depth": d.name, "path": str(d),
                                "stats": meta.get("stats", {}),
                                "files": sorted(p.name for p in d.glob("*.md"))})
        # 目录树裁剪：只带必要字段，避免前端渲染几 MB
        def slim(n):
            return {"nid": n["nid"], "level": n["level"], "title": n["title"],
                    "marker": n["marker"], "shard": n["shard"], "page_idx": n["page_idx"],
                    "n_chars": n["n_chars"], "n_blocks": n["n_blocks"],
                    "flags": n.get("flags", []),
                    "children": [slim(c) for c in n.get("children", [])]}
        pendant = next((r for r in list_pending() if r["source_id"] == sid), None)
        return {"ok": True, "source_id": sid, "project": proj,
                "pending_ingest": pendant,
                "outline": {**{k: v for k, v in outline.items() if k != "tree"},
                            "tree": [slim(n) for n in outline.get("tree", [])],
                            "printed_toc_raw": (outline.get("printed_toc") or {}).get("raw", "")},
                "calibration": calib, "verify": verify, "exports": exports,
                "work_dir": str(wd)}

    # -------------------------------------------------- 文件

    def serve_file(self, rel: str):
        """只允许读 out/ 下的文件，防目录穿越。"""
        rel = unquote(rel)
        target = (OUT_DIR / rel).resolve()
        if not str(target).startswith(str(OUT_DIR.resolve())) or not target.is_file():
            return self._err("forbidden", 403)
        ct = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ct.startswith("text/") and "charset" not in ct:
            ct += "; charset=utf-8"
        self._send(200, target.read_bytes(), ct)

    def serve_zip(self, arg: str, depth: str):
        import io
        import zipfile
        sid = resolve_source(arg)
        d = out_dir(sid) / (depth if depth.startswith("L") else f"L{depth}")
        if not d.is_dir():
            return self._err("没有该深度的导出", 404)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(d.rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(d.parent))
        name = f"{sid}_{d.name}.zip"
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f'attachment; filename="doclab.zip"')
        self.send_header("Content-Length", str(buf.tell()))
        self.end_headers()
        self.wfile.write(buf.getvalue())


class _UiServer(ThreadingHTTPServer):
    """界面服务器。

    Windows 上必须显式关掉 allow_reuse_address：
      http.server.HTTPServer 默认把它设为 1，而 Windows 的 SO_REUSEADDR
      **允许两个进程绑同一个端口**（Unix 语义不同，那边是为了重启时不被
      TIME_WAIT 挡住）。后果：旧服务没退干净时新服务照样 bind 成功，
      两个进程抢着接请求 —— 代码明明重启了，浏览器却还在跟旧进程说话，
      报出「unknown path」这类假 404；而且 serve() 的端口顺延**永远不触发**，
      因为它依赖 bind 失败。
      Unix 保持 True（快速重启），Windows 设 False 让重复 bind 如实报错。
    """
    daemon_threads = True
    allow_reuse_address = (os.name != "nt")


def serve(port: int = 8765, open_browser: bool = True, span: int = 20) -> int:
    """起界面。

    端口被占时自动往后顺延（最多试 span 个），而不是抛一堆 traceback——
    双击启动的场景下，上一次没关干净的窗口占着 8765 是常有的事，
    报「Address already in use」对使用者毫无信息量。
    """
    httpd = None
    for p in range(port, port + span):
        try:
            httpd = _UiServer(("127.0.0.1", p), Handler)
            port = p
            break
        except OSError as e:
            # WinError 10013(WSAEACCES) / 10048(WSAEADDRINUSE)、Unix 98、macOS 48
            # 注意 Windows 端口被占时报的常是 10013「权限不允许」而不是 10048，
            # 另外 Hyper-V/WSL 保留端口段被 bind 时也报 10013 —— 两种都该顺延重试。
            msg = str(e)
            if (getattr(e, "errno", None) in (13, 48, 98, 10013, 10048)
                    or "10013" in msg or "10048" in msg):
                print(f"[ui] 端口 {p} 不可用，试 {p + 1}")
                continue
            raise
    if httpd is None:
        raise SystemExit(f"[ui] {port}–{port + span - 1} 全被占用了，"
                         f"指定一个空闲端口再试：python doclab.py ui --port 9000")

    url = f"http://127.0.0.1:{port}/"
    print(f"doclab 界面：{url}")
    print(f"工作区：{WORK_DIR}")
    # 启动时把代码戳和接口清单打在黑窗里：改了后端忘了重启时，
    # 界面日志里的戳对不上这里，一眼就能看出界面在跟旧进程说话。
    print(f"代码戳：{_code_stamp()} · 接口 {len(API_CAPS)} 项（{'、'.join(API_CAPS)}）")
    print("（Ctrl-C 停止）")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0
