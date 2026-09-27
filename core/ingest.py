"""入库（ingest）：把 PDF 送进 MinerU，落成可用的工程目录。

两条路：
  ingest  调 MinerU v4 API：切分片 → 批量申请上传位 → PUT → 轮询 → 下载 zip → 解压
  adopt   沿用本机已有的 MinerU 工程目录（桌面版跑过的成果），不重跑 OCR

幂等：source_id 由文件内容哈希派生，project.json 已存在且不含 --force 时直接复用。
"""
from __future__ import annotations

import hashlib
import time
import zipfile
from pathlib import Path
from typing import Callable

from .config import Config, WORK_DIR, load_config, make_source_id, work_dir
from .mineru_api import MineruClient, MineruError
from .project import save_project, scan_shard, shard_pdf, discover_mineru_dirs
from .util import sha256_file, now_iso, read_json, write_json

Log = Callable[[str], None]

# 下载站水印，常被写进 PDF 文件名里，不该跟着进 source_id / doc_title。
# 如「认识论引论 (夏甄陶) (z-library.sk, 1lib.sk, z-lib.sk).pdf」。
_SITE_WORDS = ("z-lib", "zlib", "1lib", "z-library", "annas-archive", "anna's archive",
               "libgen", "libgenesis", "sci-hub")


def strip_site_tags(name: str) -> str:
    """去掉文件名里的下载站水印括注（括号内含下载站标识的整段删掉）。"""
    import re
    out = name
    for m in re.finditer(r"[(\（\[\【]([^)\）\]\】]*)[)\）\]\】]", name):
        if any(w in m.group(1).lower() for w in _SITE_WORDS):
            out = out.replace(m.group(0), " ")
    return re.sub(r"\s{2,}", " ", out).strip()


def _noop(_: str) -> None:
    pass


# ---------------------------------------------------------------- 解压

def _project_root_under(base: Path) -> Path | None:
    """在解压结果里找含 content_list.json 的目录（zip 可能多一层）。"""
    from .project import find_content_list
    if find_content_list(base):
        return base
    for d in sorted(p for p in base.rglob("*") if p.is_dir()):
        if find_content_list(d):
            return d
    return None


def unzip_project(zip_path: Path, dest: Path) -> Path:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    root = _project_root_under(dest)
    if root is None:
        raise MineruError(f"{zip_path.name} 解压后找不到 content_list.json")
    return root


# ---------------------------------------------------------------- 分片级流水

def plan_shards(src: Path, wd: Path, pages: int, mode: str, label: str) -> list[dict]:
    """算出分片计划。每片一个 dict，落进 ingest_state.json 当状态机的单元。

    mode="mineru"：**不切文件**。整本原始 PDF 由 MinerU 按 `page_ranges` 选页 ——
      这是「前置环节交由 MinerU 切块」。代价是同一份 PDF 要按分片数重复上传。
    mode="local"：本机 pypdf 切出 `_P1/_P2…`，只上传分片（上传流量更省，但要跑 pypdf）。
    两种模式下每片都是**独立的上传+独立的任务**，所以单片失败可以单独重做。
    """
    from .project import pdf_page_count

    total = pdf_page_count(src)
    if total == 0:
        # 读不出页数（加密/损坏）→ 单片处理，不猜范围
        return [{"tag": "P1", "page_ranges": "", "expect_pages": 0,
                 "upload_from": str(src), "name": src.name, "state": "pending"}]

    ranges = [(i + 1, min(i + pages, total)) for i in range(0, total, pages)]
    spans = [f"{a}-{b}" for a, b in ranges]

    if mode == "local" and len(spans) > 1:
        # 用清洗过的 label 当分片名，别让「(z-library.sk, 1lib.sk)」跟着进文件名
        parts = shard_pdf(src, wd / "sources", shard_pages=pages, stem=label)
        return [{"tag": f"P{i}", "page_ranges": spans[i - 1],
                 "expect_pages": ranges[i - 1][1] - ranges[i - 1][0] + 1,
                 "upload_from": str(p), "name": p.name, "state": "pending"}
                for i, p in enumerate(parts, 1)]

    out = []
    for i, span in enumerate(spans, 1):
        # 单片时不必带 page_ranges（少一个变量，也让 MinerU 自己走默认全篇）
        pr = "" if len(spans) == 1 else span
        name = src.name if len(spans) == 1 else f"{label}__{span}.pdf"
        out.append({"tag": f"P{i}", "page_ranges": pr,
                    "expect_pages": ranges[i - 1][1] - ranges[i - 1][0] + 1,
                    "upload_from": str(src), "name": name, "state": "pending"})
    return out


def _shard_submit(client, shard: dict, log: Log) -> None:
    """为**单个**分片申请上传位并上传。单片重做走这里。"""
    from .mineru_api import FileSpec

    src = Path(shard["upload_from"])
    if not src.exists():
        raise FileNotFoundError(f"分片 {shard['tag']} 的源文件不在了：{src}")
    batch_id, slots = client.request_upload_urls(
        [FileSpec(name=shard["name"], page_ranges=shard.get("page_ranges") or "")])
    client.put_file(slots[0], src)
    shard["batch_id"] = batch_id
    shard["state"] = "uploaded"
    log(f"[ingest] {shard['tag']} 已上传 {src.name}"
        + (f"（page_ranges={shard['page_ranges']}）" if shard.get("page_ranges") else ""))


def _shard_poll(client, shard: dict, log: Log) -> None:
    """轮询单片任务；done 后记下 full_zip_url。失败则置 ocr_failed（可重提）。"""
    tag = shard["tag"]

    def tick(rs):
        for r in rs:
            prog = r.extract_progress or {}
            extra = (f" {prog.get('extracted_pages','?')}/{prog.get('total_pages','?')} 页"
                     if prog else "")
            log(f"[ingest]   {tag} {r.file_name}: {r.state}{extra}")

    rs = client.poll_batch(shard["batch_id"], 1, on_tick=tick)
    r = rs[0]
    if r.failed:
        shard["state"] = "ocr_failed"
        shard["err"] = r.err_msg or "OCR 失败"
        raise MineruError(f"{tag} OCR 失败：{shard['err']}")
    shard["full_zip_url"] = r.full_zip_url
    shard["file_name"] = r.file_name
    shard["state"] = "ocr_done"


def _shard_fetch(client, shard: dict, wd: Path, log: Log) -> None:
    """下载 + 解压单片。已经有解压好的工程就直接跳过（幂等）。"""
    tag = shard["tag"]
    dest_root = wd / "projects" / tag
    if shard.get("state") == "unzipped" and _project_root_under(dest_root):
        log(f"[ingest]   {tag} 已就绪，跳过")
        return
    if not shard.get("full_zip_url"):
        raise MineruError(f"{tag} 还没有 full_zip_url，无法下载")
    zip_path = wd / "zips" / f"{tag}.zip"
    log(f"[ingest] 下载 {tag} ← {shard.get('file_name') or shard['name']} …")
    client.download(shard["full_zip_url"], zip_path)     # 自带断点续传 + 完整性校验
    log(f"[ingest]   {tag}.zip {zip_path.stat().st_size / 1048576:.1f} MB，校验收讫 ✓")
    root = unzip_project(zip_path, dest_root)
    sh = scan_shard(root, tag)
    shard.update(state="unzipped", project_dir=str(root),
                 zip_bytes=zip_path.stat().st_size, pages=sh.n_pages, blocks=sh.n_items)
    log(f"[ingest]   {tag} → {root}（{sh.n_pages} 页 / {sh.n_items} 块）")


def drive_shards(cfg: Config, wd: Path, state: dict, log: Log,
                 tries_per_shard: int = 3) -> dict:
    """逐片推进状态机。**每片独立重试**：某片炸了不牵连其他片，也不重跑已就绪的片。

    这一步是「P1/P2/P3 之一失败，不必全部重做」的落点：
      - state=="unzipped" → 直接跳过
      - 有 batch_id 但没下完 → 只补下载
      - 任务失败/没提交过 → 只为这一片重新申请上传位（新 batch）再走一遍
    """
    # 客户端**惰性**建：全都已就绪时一个请求都不该发，也就不该要求 token。
    # （原来在循环外直接 MineruClient(cfg)，导致「全片 unzipped 只差登记」这种事
    #   在没有 token 的环境里也炸，而它跟网络毫无关系。）
    _cli: dict = {}

    def client():
        if "c" not in _cli:
            _cli["c"] = MineruClient(cfg, log=log)
        return _cli["c"]

    state_path = wd / "ingest_state.json"

    for shard in state["shards"]:
        tag = shard["tag"]
        for attempt in range(1, tries_per_shard + 1):
            try:
                if shard.get("state") == "unzipped":
                    break
                if not shard.get("batch_id") or shard.get("state") in ("pending", "ocr_failed",
                                                                      "upload_failed"):
                    _shard_submit(client(), shard, log)
                if not shard.get("full_zip_url"):
                    _shard_poll(client(), shard, log)
                _shard_fetch(client(), shard, wd, log)
                break
            except Exception as e:                  # noqa: BLE001
                shard["state"] = shard.get("state") if shard.get("state") == "unzipped" else "failed"
                shard["err"] = f"{type(e).__name__}: {e}"
                log(f"[ingest] ✗ {tag} 第 {attempt}/{tries_per_shard} 次未成：{shard['err']}")
                if attempt < tries_per_shard:
                    wait = min(30, 5 * 2 ** (attempt - 1))
                    log(f"[ingest]   {tag} {wait}s 后单独重做（其他片不受影响）…")
                    time.sleep(wait)
                # 重做前清掉半成品，避免拿旧链接/旧 half-zip 继续
                shard.pop("full_zip_url", None)
                shard["state"] = "pending"
                shard["batch_id"] = ""
        write_json(state_path, state)               # 每片一落盘，随时可打断

    bad = [s for s in state["shards"] if s.get("state") != "unzipped"]
    if bad:
        # 把每片的**最后一次真实原因**带出来。只报「P2 没下来」是不够的：
        # 缺 token / 路径没了这类问题 resume 一万次也不会好，必须让人一眼看出该去改什么。
        rows = "\n".join(f"    {s['tag']}：{s.get('err') or '原因未记录'}" for s in bad)
        raise MineruError(
            f"以下分片最终没下来：{', '.join(s['tag'] for s in bad)}\n"
            f"{rows}\n"
            f"  已就绪的片不会重跑；修掉上面的原因后再次 resume，只补这些片。")
    return state


def finalize_project(cfg: Config, wd: Path, state: dict, log: Log) -> dict:
    """所有分片就绪后，登记成 source（写 project.json）并撤掉中途状态。"""
    shards_meta = [scan_shard(Path(s["project_dir"]), s["tag"]).to_dict()
                   for s in state["shards"]]
    project = {
        "source_id": state["source_id"],
        "doc_title": state.get("title") or state.get("doc_title") or Path(state["source_file"]).stem,
        "source_file": state["source_file"],
        "ingested_at": now_iso(),
        "mode": "mineru-api",
        "batch_id": state["shards"][0].get("batch_id", ""),
        "api_base": cfg.api_base,
        "shard_mode": state.get("shard_mode", "mineru"),
        "params": {"language": cfg.language, "enable_formula": cfg.enable_formula,
                   "enable_table": cfg.enable_table, "is_ocr": cfg.is_ocr,
                   "model_version": cfg.model_version,
                   "shard_pages": state.get("shard_pages", cfg.shard_pages)},
        "shards": shards_meta,
    }
    if state.get("digest"):
        project["source_sha256_headtail"] = state["digest"]
    save_project(wd, project)
    (wd / "ingest_state.json").unlink(missing_ok=True)
    log(f"[ingest] 完成：source_id={state['source_id']}")
    return project


def resume_ingest(batch_id: str | None = None, cfg: Config | None = None,
                  log: Log = _noop, source_id: str | None = None) -> dict:
    """续跑没落地的 ingest（下载/单片失败后）。

    靠 `_work/*/ingest_state.json` 找回 —— 状态落盘，批次号不再只活在控制台日志里。
    可以按 batch_id 找，也可以按 source_id 找，或都不给（只有一个未完成时就它）。
    """
    cfg = cfg or load_config()
    cands = []
    for pj in (sorted(WORK_DIR.iterdir()) if WORK_DIR.is_dir() else []):
        st = read_json(pj / "ingest_state.json", None)
        if not st:
            continue
        if batch_id and not any(s.get("batch_id") == batch_id for s in st.get("shards", [])):
            continue
        if source_id and st.get("source_id") != source_id:
            continue
        cands.append((pj, st))

    if not cands:
        raise FileNotFoundError(
            f"找不到未完成的 ingest 记录（batch_id={batch_id} source_id={source_id}）。"
            "看 _work/*/ingest_state.json；旧版本跑过的批次没写这个文件，只能重跑 OCR。")
    if len(cands) > 1 and not (batch_id or source_id):
        names = ", ".join(st.get("source_id", "?") for _, st in cands)
        raise MineruError(f"有多个未完成的 ingest，请指定 source_id：{names}")

    wd, state = cands[0]
    done = sum(1 for s in state["shards"] if s.get("state") == "unzipped")
    log(f"[resume] {state['source_id']}：{len(state['shards'])} 片，已就绪 {done} 片，续跑其余")
    drive_shards(cfg, wd, state, log)
    return finalize_project(cfg, wd, state, log)


# ---------------------------------------------------------------- API 入库

def ingest_local(src: Path, cfg: Config, shard_pages: int | None = None,
                 force: bool = False, log: Log = _noop,
                 title: str | None = None, shard_mode: str | None = None) -> dict:
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(src)
    suffix = src.suffix.lower()
    if suffix == ".epub":
        return ingest_epub(src, cfg, force=force, log=log, title=title)
    if suffix != ".pdf":
        raise NotImplementedError(
            f"只支持 PDF 与 EPUB（收到 {src.suffix}）。其他格式请先转成 PDF。"
        )

    digest = sha256_file(src)
    label = strip_site_tags(src.stem) or src.stem
    sid = make_source_id(label, digest)
    wd = work_dir(sid)
    wd.mkdir(parents=True, exist_ok=True)

    existing = read_json(wd / "project.json", None)
    if existing and not force:
        log(f"[ingest] 复用已有工程 {sid}（{len(existing.get('shards', []))} 片）；要重跑加 --force")
        return existing

    # 上次跑到一半 → 直接续跑（不重烧 OCR）
    stale = read_json(wd / "ingest_state.json", None)
    if stale and stale.get("shards"):
        log(f"[ingest] 发现未完成的 ingest，续跑（不重跑已就绪的分片）")
        return resume_ingest(cfg=cfg, log=log, source_id=sid)

    mode = (shard_mode or cfg.shard_mode or "mineru").lower()
    if mode not in ("mineru", "local"):
        mode = "mineru"
    pages = shard_pages or cfg.shard_pages
    plan = plan_shards(src, wd, pages, mode, label)
    log(f"[ingest] {src.name}：{len(plan)} 个分片（每片上限 {pages} 页）｜"
        f"切块方式 = {'交由 MinerU（page_ranges）' if mode == 'mineru' else '本机 pypdf'}")

    state = {
        "source_id": sid, "source_file": str(src), "title": title or "",
        "doc_title": label, "digest": digest, "shard_pages": pages,
        "shard_mode": mode, "submitted_at": now_iso(), "shards": plan,
    }
    write_json(wd / "ingest_state.json", state)

    drive_shards(cfg, wd, state, log)
    return finalize_project(cfg, wd, state, log)


# ---------------------------------------------------------------- EPUB 入库

def ingest_epub(src: Path, cfg: Config, force: bool = False, log: Log = _noop,
                title: str | None = None) -> dict:
    """EPUB 入库：本机解析成块序列，**不调 MinerU、不烧配额**。

    为什么单开一条路：EPUB 自带文本与标题标签，没有「识别」这一步可做 ——
    送进 OCR 只会把干净的电子文本重新猜一遍。产出的仍是 MinerU 形态的工程目录
    （content_list.json + images/），所以下游 outline / pagecal / export 一行不用改。

    代价必须有言在先：**EPUB 没有纸书页码**。能拿到的只有 spine 阅读顺序单元，
    所以页码校准必然降级 section_only，页锚写 `p=pdf-N unmapped`，绝不拿原版边码
    （`F7` / `E423`）冒充 —— 那是外文书的页码，不是这本书的（见 core/epub.py）。

    不写 ingest_state.json：那条路是给「OCR 跑到一半被中断」留断点的，EPUB 全程
    本机几秒钟跑完，没有可续的批次。
    """
    from .epub import build_source as build_epub_source

    if src.suffix.lower() != ".epub":
        # 这个函数是公开入口（界面/CLI 之外也可能被调），自己也要拦；
        # 否则一份 .txt 会被丢进 zipfile，报出来的是一句看不懂的 BadZipFile。
        raise NotImplementedError(f"ingest_epub 只接 .epub（收到 {src.suffix}）")

    digest = sha256_file(src)
    label = strip_site_tags(src.stem) or src.stem
    sid = make_source_id(label, digest)
    wd = work_dir(sid)
    wd.mkdir(parents=True, exist_ok=True)

    existing = read_json(wd / "project.json", None)
    if existing and not force:
        log(f"[ingest] 复用已有工程 {sid}（EPUB，{len(existing.get('shards', []))} 片）；"
            f"要重跑加 --force")
        return existing

    log(f"[ingest] {src.name}：EPUB 本机解析（不走 MinerU）")
    # cfg 只用来保持一致签名；EPUB 路径不读 token / 不分片 / 不产生 API 调用。
    _ = cfg
    return build_epub_source(src, wd, sid, title or label, log=log)


# ---------------------------------------------------------------- 移出工作台

def remove_source(sid: str, log: Log = _noop) -> dict:
    """把一个源移出工作台：`_work/<sid>` 与 `out/<sid>` 整体移入 `_trash`。

    只动 doclab 自己生成的产物；**原始 PDF 与 MinerU 本机工程目录一律不碰**。
    用「移动」而不是直接删，是留后悔药：目录树/页码校准/导出重算代价不大，
    但 OCR 结果重跑要烧 MinerU 配额和几十分钟，不该有一次误点就没了。
    要彻底清空，手工删 `_trash` 即可。
    """
    import shutil
    from datetime import datetime
    from .config import TRASH_DIR, work_dir as _wd, out_dir as _od

    sid = (sid or "").strip()
    if not sid or "/" in sid or "\\" in sid or sid in (".", ".."):
        raise ValueError(f"非法 source_id：{sid!r}")
    if not (WORK_DIR / sid).is_dir():
        raise FileNotFoundError(f"没有这个源：{sid}")

    box = TRASH_DIR / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}__{sid}"
    box.mkdir(parents=True, exist_ok=True)
    moved = []
    for d, label in ((_wd(sid), "work"), (_od(sid), "out")):
        if d.is_dir():
            dest = box / label
            shutil.move(str(d), str(dest))
            moved.append({"from": str(d), "to": str(dest)})
            log(f"[remove] {d} → {dest}")
    if not moved:
        log(f"[remove] {sid} 没有可移走的产物（目录已空）")
    log(f"[remove] 原始 PDF / MinerU 工程未动；回收站在 {box}（可手工恢复）")
    return {"source_id": sid, "moved": moved, "trash": str(box)}


# ---------------------------------------------------------------- 沿用已有工程

def adopt_existing(dirs: list[Path], cfg: Config | None = None, source_id: str | None = None,
                   title: str | None = None, force: bool = False, log: Log = _noop,
                   keyword: str | None = None) -> dict:
    """把本机已有的 MinerU 工程目录登记成一个 source。

    分片标签按目录名尾部 `_P1/_P2/...` 自然排序；无标记则按目录名排序。
    """
    import re
    if not dirs and keyword:
        root = (cfg.mineru_output_root if cfg else Path.home() / "MinerU")
        dirs = discover_mineru_dirs(root, keyword)
    dirs = [Path(d) for d in dirs]
    if not dirs:
        raise FileNotFoundError("没有可登记的 MinerU 工程目录")

    def tag_of(p: Path, i: int) -> str:
        m = re.search(r"_P(\d+)$", p.name)
        if m:
            return f"P{m.group(1)}"
        m2 = re.match(r"^(\d+)", p.name)
        if m2:
            return f"P{m2.group(1)}"
        return f"P{i}"

    def sort_key(p: Path):
        m = re.search(r"_P(\d+)$", p.name)
        return (0, int(m.group(1))) if m else (1, p.name)

    dirs.sort(key=sort_key)
    pairs = [(tag_of(d, i), d) for i, d in enumerate(dirs, 1)]

    # 目录名清洗。本机 MinerU 工程目录名基本是 Zotero 导出体例
    # 「作者 - 年份 - 标题.pdf-<uuid>」或「标题_P1.pdf-<uuid>」，不清的话
    # source_id 会长成「黄艳红---2017---记忆之场-….pdf.黄艳红 - 2017」这种垃圾。
    # 顺序要紧：uuid 必须最先剥，否则 `.pdf` 后面还挂着 uuid，扩展名匹配不到。
    clean = dirs[0].name
    clean = re.sub(r"-[0-9a-f]{8}-[0-9a-f-]{27,}$", "", clean)      # 1) uuid 尾巴
    clean = re.sub(r"\.(pdf|epub|docx?|txt)$", "", clean, flags=re.I)  # 2) 扩展名
    clean = re.sub(r"[_\-\s]*P\d+$", "", clean)                     # 3) 分片后缀
    clean = re.sub(r"^\s*[^-]{1,24}\s+-\s+(?:19|20)\d{2}\s+-\s+", "", clean).strip()  # 4) 作者 - 年份 -
    label = (title or "").strip() or clean or "adopted"

    if not source_id:
        # digest 必须由内容派生。旧实现在目录情况下把名字自己当 digest 传进去，
        # 于是 id 尾巴永远是名字前 10 个字，既不去重也不稳定。
        dig = hashlib.sha1(
            "\n".join(sorted(str(d.resolve()) for d in dirs)).encode("utf-8")).hexdigest()
        source_id = make_source_id(label, dig)
    wd = work_dir(source_id)
    wd.mkdir(parents=True, exist_ok=True)
    if read_json(wd / "project.json", None) and not force:
        log(f"[adopt] 已存在 {source_id}，复用（加 --force 覆盖登记）")

    shards_meta = []
    for tag, d in pairs:
        sh = scan_shard(d, tag)
        shards_meta.append(sh.to_dict())
        log(f"[adopt] {tag} ← {d.name}（{sh.n_pages} 页 / {sh.n_items} 块）")

    project = {
        "source_id": source_id,
        "doc_title": label,
        "source_file": str(dirs[0]),
        "ingested_at": now_iso(),
        "mode": "adopt-existing",
        "shards": shards_meta,
    }
    save_project(wd, project)
    log(f"[adopt] 完成：source_id={source_id}")
    return project
