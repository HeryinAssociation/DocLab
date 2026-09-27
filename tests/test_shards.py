"""分片计划与分片级状态机单测（**不发任何网络请求**）。

覆盖用户提的两件事：
  1. 前置切块：mineru 模式整本上传 + page_ranges；local 模式本机切好再传。
  2. 断点：某片失败不该牵连其他片；全片就绪时即便没有 token 也应能收尾。

    python tests/test_shards.py
"""
from __future__ import annotations

import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pypdf 解析故意做坏的文件时会往 stderr 喷 "invalid pdf header" —— 那是被测试的预期行为，
# 不是测试失败；静音掉，免得盖住真正的结论。
logging.getLogger("pypdf").setLevel(logging.CRITICAL)

from core.config import Config                                                         # noqa: E402
from core.ingest import drive_shards, plan_shards, strip_site_tags                     # noqa: E402

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        FAILS.append(msg)


def make_pdf(path: Path, n: int) -> None:
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(n):
        w.add_blank_page(width=200, height=300)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        w.write(f)


def fake_project(root: Path, pages: int) -> None:
    """造一个能过 scan_shard 的最小 MinerU 工程目录。"""
    root.mkdir(parents=True, exist_ok=True)
    items = [{"type": "text", "text": f"p{i}", "page_idx": i} for i in range(pages)]
    (root / "content_list.json").write_text(
        json.dumps(items, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="doclab_shards_"))
    try:
        src = tmp / "认识论引论 (夏甄陶) (z-library.sk, 1lib.sk, z-lib.sk).pdf"
        make_pdf(src, 457)
        label = strip_site_tags(src.stem)

        print("【1】文件名水印清洗")
        check("z-library" not in label and "夏甄陶" in label,
              f"strip_site_tags → {label!r}")

        wd = tmp / "_work" / "sid"
        wd.mkdir(parents=True, exist_ok=True)

        print("\n【2】mineru 模式（整本上传，交由 MinerU 按页范围切）")
        plan = plan_shards(src, wd, 200, "mineru", label)
        check(len(plan) == 3, f"457 页 / 200 → {len(plan)} 片（期望 3）")
        check([s["page_ranges"] for s in plan] == ["1-200", "201-400", "401-457"],
              f"page_ranges = {[s['page_ranges'] for s in plan]}")
        check(all(s["upload_from"] == str(src) for s in plan),
              "三片都指向同一份原始 PDF（不本机切文件）")
        check(all(s["state"] == "pending" for s in plan), "初始状态 pending")

        print("\n【3】local 模式（本机 pypdf 切好再上传）")
        plan_l = plan_shards(src, wd, 200, "local", label)
        check(len(plan_l) == 3, f"{len(plan_l)} 片")
        paths = [Path(s["upload_from"]) for s in plan_l]
        check(all(p.exists() and p != src for p in paths), "三片各自落了实体文件")
        check(len({p for p in paths}) == 3, "三片文件互不相同")
        from pypdf import PdfReader
        sizes = [len(PdfReader(str(p)).pages) for p in paths]
        check(sizes == [200, 200, 57], f"分片页数 {sizes}（期望 [200, 200, 57]）")
        check("z-library" not in paths[0].name, f"分片名已清干净：{paths[0].name}")

        print("\n【4】边界：不超阈 / 读不出页数")
        small = tmp / "small.pdf"
        make_pdf(small, 150)
        p1 = plan_shards(small, wd, 200, "mineru", "small")
        check(len(p1) == 1 and p1[0]["page_ranges"] == "",
              "单片不带 page_ranges（少一个变量，也少一次被 API 挑刺的机会）")
        exact = tmp / "exact.pdf"
        make_pdf(exact, 200)
        check(len(plan_shards(exact, wd, 200, "mineru", "exact")) == 1, "恰好 200 页 → 1 片")
        broken = tmp / "broken.pdf"
        broken.write_bytes(b"not a pdf at all")
        pb = plan_shards(broken, wd, 200, "mineru", "broken")
        check(len(pb) == 1 and pb[0]["page_ranges"] == "",
              "读不出页数 → 不猜范围，单片交给 MinerU")

        print("\n【5】全片就绪时应能离线收尾（不发请求、不要 token）")
        wd2 = tmp / "_work" / "sid2"
        wd2.mkdir(parents=True, exist_ok=True)
        st = {"source_id": "sid2", "source_file": str(src), "doc_title": label,
              "shard_mode": "mineru", "shard_pages": 200, "shards": []}
        for tag in ("P1", "P2", "P3"):
            d = wd2 / "projects" / tag
            fake_project(d, 5)
            st["shards"].append({"tag": tag, "state": "unzipped", "page_ranges": "1-5",
                                 "project_dir": str(d), "pages": 5, "blocks": 5})
        cfg = Config(token="")          # ← 刻意不给 token
        logs: list[str] = []
        try:
            drive_shards(cfg, wd2, st, logs.append)
            check(True, "drive_shards 在无 token 下走完（客户端从未被构造）")
        except Exception as e:                                  # noqa: BLE001
            check(False, f"drive_shards 无 token 时炸了：{type(e).__name__}: {e}")

        print("\n【6】有片未就绪 → 必须要求 token，且失败片可被单独指认")
        st["shards"][1]["state"] = "pending"
        try:
            drive_shards(cfg, wd2, st, logs.append, tries_per_shard=1)
            check(False, "缺 token 却跑过了，应当报错")
        except Exception as e:                                  # noqa: BLE001
            check("token" in str(e).lower(), f"缺 token 时报错明确：{str(e)[:60]}")
        check([s["tag"] for s in st["shards"] if s.get("state") != "unzipped"] == ["P2"],
              "只有 P2 被标为未就绪（P1/P3 未被牵连）")
        check((wd2 / "ingest_state.json").exists(), "每片跑完即落盘 ingest_state.json")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if FAILS:
        print(f"\n✗ {len(FAILS)} 项未过")
        return 1
    print("\n✓ 分片计划与断点逻辑全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
