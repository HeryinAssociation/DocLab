"""端到端：走**界面后端**补一个漏掉的标题（真实书籍的副本，不碰原书）。

为什么要单独有这个测试：tests/test_add.py 直接调 core 的函数，覆盖不到
「界面点下去 → HTTP → 落盘 → 重算 → 导出」这条链。这一层出问题的方式很隐蔽：
后端存了、界面没显示；或者界面显示了、重算时没带上（act_outline 忘了传 manual_added）。
那种错在单测里全绿，用户一用就发现。

做法：把 `_work/<真书>` 整份**复制**成一个临时源（project.json 里的分片路径是绝对的，
副本照旧指向同一个 MinerU 工程），在副本上跑完整流程，跑完删掉。
原书的 _work / out / manual_edits.json 一个字节都不动。

    python tests/test_add_e2e.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import ROOT, load_config, out_dir, work_dir          # noqa: E402
from core.project import iter_blocks, is_noise_text, load_shards       # noqa: E402
from core.outline import build_tree, walk_nodes                        # noqa: E402
from core.pagecal import calibrate, load_calibration                   # noqa: E402

SRC = "中国数字人文发展报告"
TMP = "_zz_add.0000000000"
PORT = 8801
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
FAILS: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(name)


def get(path: str) -> dict:
    with opener.open(f"http://127.0.0.1:{PORT}{path}", timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with opener.open(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_job(jid: str, timeout: float = 300) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = get(f"/api/job?id={jid}")
        if j.get("status") in ("done", "error"):
            return j
        time.sleep(0.4)
    raise TimeoutError(jid)


def btext(b) -> str:
    return b.text or b.table_body or ""


def pick_target():
    """挑一个「MinerU 漏掉了标题」式的落点：真正文里、单行、没立过标题的一整段。"""
    wd = work_dir(SRC)
    shards = load_shards(wd)
    blocks = iter_blocks(shards)
    calib = calibrate(SRC, blocks, [s.to_dict() for s in shards],
                      anchors={})
    roots, _ = build_tree(SRC, blocks, doc_title="t", calib=calib)
    ns = [n for r in roots for n in walk_nodes(r)]
    in_tree = {n.gid_start for n in ns}
    front_end = max(n.gid_end for n in ns if n.marker == "front")
    for b in blocks:
        t = btext(b)
        if (b.type == "text" and b.gid not in in_tree and b.gid > front_end
                and 150 <= len(t) <= 300 and "\n" not in t
                and not is_noise_text(t) and t.strip()):
            return b
    raise RuntimeError("这本书里挑不出合适的落点")


def setup() -> None:
    cleanup()
    src, dst = work_dir(SRC), work_dir(TMP)
    # dirs_exist_ok=True：cleanup() 若是没删掉（环境删除保护），这里不能再撞
    # FileExistsError —— 那会把「清理没成功」误报成「用例失败」。覆盖写即可，
    # 副本内容本来就整份来自 SRC。
    shutil.copytree(src, dst, dirs_exist_ok=True)
    pj = json.loads((dst / "project.json").read_text(encoding="utf-8"))
    pj["source_id"] = TMP
    pj["doc_title"] = "冒烟副本"
    (dst / "project.json").write_text(json.dumps(pj, ensure_ascii=False), encoding="utf-8")
    out_dir(TMP).mkdir(parents=True, exist_ok=True)


def cleanup() -> None:
    """清掉冒烟副本的工作目录与产物。

    不能只写 `ignore_errors=True`：工作区对批量删除有保护，被挡住时抛的不是 OSError，
    ignore_errors 拦不住 —— 结果断言全过了、末尾清理一炸，整个用例判红。
    清理失败只是「临时目录没删掉」，绝不该当成产品缺陷。这里一律吞掉并提示。
    这个副本目录本身就必须落在工作区里（测的正是产品的存储层），挪不走。
    """
    for d in (work_dir(TMP), out_dir(TMP)):
        try:
            shutil.rmtree(d, ignore_errors=True)
        except BaseException as e:                              # noqa: BLE001
            print(f"    （清不掉 {d}：{type(e).__name__}，跳过）")


def md_files() -> list[Path]:
    """正文文件。00-目录.md 也在这个目录里，但它没有页锚、也不是正文，必须排掉。"""
    d = out_dir(TMP) / "L2"
    return sorted(p for p in d.glob("*.md") if p.name != "00-目录.md") if d.is_dir() else []


def port_busy(port: int) -> bool:
    """端口上已经有人（多半是上一次测试残留的服务进程）。"""
    import socket
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main() -> int:
    if port_busy(PORT):
        print(f"✗ 端口 {PORT} 已被占用 —— 多半是上一次测试残留的服务。\n"
              f"  查：netstat -ano | findstr {PORT}\n"
              f"  杀：taskkill /PID <那个 PID> /F")
        return 1
    target = pick_target()
    t = btext(target)
    print(f"落点：gid {target.gid}（{target.shard} p{target.page_idx}，{len(t)} 字）"
          f"  {t[:34]}…")
    setup()
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "doclab.py"), "ui", "--port", str(PORT), "--no-browser"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                get("/api/bootstrap")
                break
            except Exception:                                  # noqa: BLE001
                time.sleep(0.25)
        else:
            print("✗ 界面起不来")
            return 1

        # 1) 正文块视图：拿正文里那句话搜得到它
        r = get(f"/api/blocks?source={TMP}&q={urllib.parse.quote(t[:14])}&offset=0&limit=20")
        row = next((x for x in r["rows"] if x["gid"] == target.gid), None)
        check("界面能按正文原文搜到那一块", row is not None,
              f'{r["total"]} 块命中')
        if row is None:
            return 1
        check("那一块标着「还没立过标题」", not row["in_tree"] and not row["added"])

        # 2) 新增
        post("/api/edit/add", {"source": TMP, "gid": target.gid,
                               "title": "第X节 测试补标题", "level": 2, "offset": 0})
        e = get(f"/api/edit?source={TMP}")
        check("人工新增写进了叠加层", str(target.gid) in e["added"],
              json.dumps(e["added"].get(str(target.gid)), ensure_ascii=False))
        pend = next((n for n in e["nodes"] if n.get("pending_add")), None)
        check("重算之前，目录列表里就有这条「待算」的新增行",
              pend is not None and pend["key"] == f"add:{target.gid}"
              and pend["title"] == "第X节 测试补标题", str(pend and pend["key"]))
        check("待算行的级数＝人点的那一级", pend and pend["level"] == 2, str(pend and pend["level"]))

        # 3) 应用并重算（界面上的按钮）
        j = wait_job(post("/api/apply", {"source": TMP})["job"])
        check("「应用并重算」跑通", j["status"] == "done", j.get("error", ""))
        if j["status"] == "done":
            check("节点数 +1", j["result"]["node_total"] == 416,
                  str(j["result"]["node_total"]))
            check("重算日志里点出「人工新增标题 1 条」",
                  any("人工新增标题 1 条" in x for x in j["log"]),
                  " / ".join(x for x in j["log"] if "新增" in x)[:90])

        e2 = get(f"/api/edit?source={TMP}")
        add_row = next((n for n in e2["nodes"] if n["key"] == f"add:{target.gid}"), None)
        check("重算之后它进树了（不再是待算行）",
              add_row is not None and not add_row.get("pending_add"),
              str(add_row and (add_row["key"], add_row["level"], add_row["title"])))
        check("进树后级数还是人给的那一级", add_row and add_row["level"] == 2)
        b2 = get(f"/api/blocks?source={TMP}&q={urllib.parse.quote(t[:14])}&limit=20")
        r2 = next((x for x in b2["rows"] if x["gid"] == target.gid), None)
        check("那一块现在标成「已新增」且已在树里",
              r2 is not None and r2["added"] and r2["in_tree"])

        # 4) 导出：断点后面那个文件从这一段的开头起
        j = wait_job(post("/api/export", {"source": TMP, "depth": 2, "no_images": True})["job"])
        check("导出跑通", j["status"] == "done", j.get("error", ""))
        files = md_files()
        hit = [p for p in files if "第X节 测试补标题" in p.read_text(encoding="utf-8")[:600]]
        check("新增标题切出了自己的文件", len(hit) == 1, f"{len(hit)} 个")
        if hit:
            txt = hit[0].read_text(encoding="utf-8")
            lines = txt.split("\n")
            head = next(i for i, x in enumerate(lines) if x.startswith("<!-- p="))
            segs = [x for x in lines[head:] if x.strip() and not x.startswith("<!--")]
            check("文件正文第一行就是这一段原文（一字没动）",
                  bool(segs) and segs[0].strip().startswith(t[:20]),
                  segs[0][:26] if segs else "（空）")
            prev = files[files.index(hit[0]) - 1] if files.index(hit[0]) else None
            check("前一个文件不再含这一段",
                  prev is None or t[:24] not in prev.read_text(encoding="utf-8"))
            check("整本书里这一段只出现一次",
                  sum(p.read_text(encoding="utf-8").count(t[:24]) for p in files) == 1)

        # 5) 撤销 → 回到原样
        post("/api/edit/add-clear", {"source": TMP, "gid": target.gid})
        e3 = get(f"/api/edit?source={TMP}")
        check("撤销后叠加层里没有了", str(target.gid) not in e3["added"])
        j = wait_job(post("/api/apply", {"source": TMP})["job"])
        check("撤销后节点数回到 415",
              j["status"] == "done" and j["result"]["node_total"] == 415,
              str(j.get("result", {}).get("node_total")))
        j = wait_job(post("/api/export", {"source": TMP, "depth": 2, "no_images": True})["job"])
        hit = [p for p in md_files()
               if "第X节 测试补标题" in p.read_text(encoding="utf-8")[:600]]
        check("撤销后那个文件也没了（导出与目录同步）", not hit, f"{len(hit)} 个")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        cleanup()

    print("\n" + ("全通过：界面上补一个漏掉的标题，一路到导出与撤销，正文一字不动"
                  if not FAILS else f"{len(FAILS)} 项失败：{FAILS}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
