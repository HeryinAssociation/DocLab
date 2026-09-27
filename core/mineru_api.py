"""MinerU v4 API 客户端（纯标准库 urllib，无第三方依赖）。

流程（本地文件）：
    POST /file-urls/batch      → 取 batch_id + 每条一个预签名 OSS 上传地址
    PUT  <预签名地址>           → 直接推二进制（不带 Authorization 头）
    GET  /extract-results/batch/{batch_id}  → 轮询 state，done 后取 full_zip_url
    GET  <full_zip_url>        → 下载结果 zip

另有按 URL 提交的单任务接口（/extract/task），用于已有公网链接的源。
"""
from __future__ import annotations

import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .config import Config


class MineruError(RuntimeError):
    def __init__(self, msg: str, code: int | None = None, trace_id: str = ""):
        super().__init__(f"[mineru code={code} trace={trace_id}] {msg}" if code is not None else msg)
        self.code = code
        self.trace_id = trace_id


@dataclass
class UploadSlot:
    name: str
    url: str
    data_id: str = ""


@dataclass
class FileSpec:
    """一次上传申请里的一个文件条目。

    `page_ranges` 非空时交给 MinerU 做页选择（前置环节「交由 MinerU 切块」走这条）。
    """
    name: str
    page_ranges: str = ""


@dataclass
class TaskResult:
    file_name: str
    state: str
    full_zip_url: str = ""
    err_msg: str = ""
    extract_progress: dict = field(default_factory=dict)

    @property
    def done(self) -> bool:
        return self.state == "done"

    @property
    def failed(self) -> bool:
        return self.state in ("failed", "error")


Progress = Callable[[str], None]


def _noop(_: str) -> None:
    pass


class MineruClient:
    def __init__(self, cfg: Config, log: Progress = _noop):
        if not cfg.token:
            raise MineruError(
                "未找到 MinerU token。请设置环境变量 MINERU_TOKEN，"
                f"或写入 {os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env.local')}"
            )
        self.cfg = cfg
        self.log = log

    # ------------------------------------------------------------ 底层

    def _request(self, url: str, method: str = "GET", body: dict | None = None,
                 timeout: int = 60, auth: bool = True) -> dict:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if auth:
            req.add_header("Authorization", f"Bearer {self.cfg.token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "*/*")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            raise MineruError(f"HTTP {e.code}: {raw[:300]}", code=e.code)
        except Exception as e:  # 网络层
            raise MineruError(f"请求失败 {url}: {e!r}")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise MineruError(f"响应非 JSON: {raw[:300]}")

    def _api(self, path: str, method: str = "GET", body: dict | None = None) -> dict:
        resp = self._request(self.cfg.api_base.rstrip("/") + path, method, body)
        code = resp.get("code")
        if code != 0:
            raise MineruError(resp.get("msg", "unknown"), code=code,
                              trace_id=resp.get("trace_id", ""))
        return resp.get("data", {}) or {}

    # ------------------------------------------------------------ 批量上传

    def request_upload_urls(self, files: Iterable[Path | "FileSpec"]) -> tuple[str, list[UploadSlot]]:
        """申请上传位。每项可以是 Path，也可以是带 `page_ranges` 的 FileSpec。

        `page_ranges` 是本 API 的**逐文件**字段（逗号分隔，如 `"1-200,401-457"`）。
        实测（2026-09-20）：MinerU **不按上传文件的总页数校验** 200 页上限，
        该上限作用在「被抽取的页」上 —— 把 457 页整本传上去、`page_ranges="1-2"`，
        任务照样跑完（progress 显示 total_pages=2）。
        所以「切块」这一步可以完全交给 MinerU：同一份原始 PDF 传 N 份、
        每份给不同的 page_ranges，得到 N 个互相独立、可各自重试的结果。
        """
        items, names = [], []
        for f in files:
            spec = f if isinstance(f, FileSpec) else FileSpec(name=Path(f).name)
            entry = {"name": spec.name, "is_ocr": bool(self.cfg.is_ocr)}
            if spec.page_ranges:
                entry["page_ranges"] = spec.page_ranges
            if self.cfg.model_version:
                entry["model_version"] = self.cfg.model_version
            items.append(entry)
            names.append(spec.name)
        body = {
            "enable_formula": bool(self.cfg.enable_formula),
            "enable_table": bool(self.cfg.enable_table),
            "language": self.cfg.language,
            "files": items,
        }
        data = self._api("/file-urls/batch", "POST", body)
        batch_id = data.get("batch_id", "")
        urls = data.get("file_urls", []) or []
        if not batch_id or len(urls) != len(items):
            raise MineruError(f"上传申请返回异常: {str(data)[:300]}")
        slots = [UploadSlot(name=names[i], url=urls[i]) for i in range(len(items))]
        return batch_id, slots

    def put_file(self, slot: UploadSlot, path: Path) -> None:
        """PUT 二进制到预签名地址。

        ⚠️ 关键坑（实测踩出来的）：MinerU 返回的 OSS 预签名 URL 是按
        **Content-Type 为空**签的，而阿里云 OSS 的 V1 签名串里包含 Content-Type。
        请求里只要出现 Content-Type（哪怕值是 application/pdf，或是 urllib 给带 body
        的请求自动补的 application/x-www-form-urlencoded），签名立刻不匹配，返回
        403 SignatureDoesNotMatch。用 curl 时对应 `-H "Content-Type:"`。

        所以这里不能用 urllib —— 必须用 http.client 手工构造，只发
        Host / Accept-Encoding / Content-Length 三个头。

        另一个坑：不要带 Authorization，签名已含在 URL 里。
        """
        data = Path(path).read_bytes()
        parts = urllib.parse.urlsplit(slot.url)
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        cls = (http.client.HTTPSConnection if parts.scheme == "https"
               else http.client.HTTPConnection)
        conn = cls(parts.hostname, parts.port, timeout=600)
        try:
            conn.request("PUT", target, body=data,
                         headers={"Content-Length": str(len(data))})
            resp = conn.getresponse()
            body = resp.read()
            if resp.status not in (200, 201, 203, 204):
                raise MineruError(
                    f"上传 {slot.name} 失败 HTTP {resp.status}: {body[:300]!r}")
        except OSError as e:
            raise MineruError(f"上传 {slot.name} 连接失败: {e!r}")
        finally:
            conn.close()

    def poll_batch(self, batch_id: str, expected: int,
                   on_tick: Callable[[list[TaskResult]], None] | None = None) -> list[TaskResult]:
        deadline = time.time() + self.cfg.poll_timeout
        last_sig = None
        while True:
            data = self._api(f"/extract-results/batch/{batch_id}")
            raw = data.get("extract_result", []) or []
            results = [
                TaskResult(
                    file_name=it.get("file_name", ""),
                    state=it.get("state", "unknown"),
                    full_zip_url=it.get("full_zip_url", "") or "",
                    err_msg=it.get("err_msg", "") or "",
                    extract_progress=it.get("extract_progress", {}) or {},
                )
                for it in raw
            ]
            sig = tuple((r.file_name, r.state) for r in results)
            if sig != last_sig:
                last_sig = sig
                if on_tick:
                    on_tick(results)
            if len(results) >= expected and all(r.done or r.failed for r in results):
                return results
            if time.time() > deadline:
                raise MineruError(f"轮询超时（{self.cfg.poll_timeout}s）；已完成 "
                                  f"{sum(r.done for r in results)}/{expected}")
            time.sleep(self.cfg.poll_interval)

    def download(self, url: str, dest: Path, retries: int = 5,
                 expect_zip: bool = True, stall_timeout: int = 120) -> Path:
        """下载结果文件：断点续传 + 完整性校验。

        ⚠️ 实测踩过的两个坑（2026-09-20，认识论引论那本，3 片 9.5/9.4/2.9 MB）：

        **坑一：静默截断。** 轮询到 state=done 后立刻取 full_zip_url，CDN 侧对象可能
        还没复制完，会提前给 EOF。而最初实现 `read` 到空就当成功，于是留下一个没有
        中央目录（EOCD）的 zip，直到 `zipfile.ZipFile()` 才炸
        `BadZipFile: File is not a zip file`——错因被推到解压那步，看起来像下到了坏包。

        **坑二：连接僵住。** 比截断阴险得多：socket 不断也不给数据。P2 就死在
        **恰好 6,291,456 字节（6 MiB）**，`read()` 一直阻塞；而 `timeout=900` 意味着
        要干等 15 分钟才报错。整数 MiB 的卡点说明是本机代理在分块边界上卡住了。

        所以修法不是「重下一次」而是**断点续传**——CDN 支持 Range
        （`Accept-Ranges: bytes`，206 + `Content-Range: n-m/total`，实测可随机访问）：
          ① `stall_timeout` 是**单次 read 的最大等待**（不是总时长），僵住即抛错；
          ② 每轮开始以磁盘上已收字节为准发 `Range: bytes=N-`，接着下，不重头来；
          ③ 收满 total（或响应没给长度时）再做 zip EOCD 校验；
          ④ 校验过才 `os.replace` 原子落位，半截文件永不交给下游。
        对「提前 EOF」和「僵住」两种形态都能单调收敛。
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        # 刻意**不**清空已存在的 .part：上一轮被 Ctrl-C / 卡死留下的字节还能接着用。
        # 代价是可能撞上「陈旧 .part + 换过的对象」，所以 416 和「收满却不是 zip」
        # 两种情况都要把 .part 丢掉重来，否则会永远卡在坏偏移上。
        total: int | None = None
        last = ""
        for attempt in range(1, retries + 1):
            # 以磁盘实际字节为准（异常可能在写盘中途抛出），据此决定 Range 起点
            got = tmp.stat().st_size if tmp.exists() else 0
            try:
                req = urllib.request.Request(
                    url, headers={"Range": f"bytes={got}-"} if got else {})
                try:
                    resp = urllib.request.urlopen(req, timeout=stall_timeout)
                except urllib.error.HTTPError as e:
                    # 416 说明 Range 越界（.part 比远端对象还长 → 陈旧），丢掉重来。
                    # 注意它必须在 urlopen 那一层接：4xx 会被 urlopen 直接抛成
                    # HTTPError，走不进下面的 `with resp`。
                    if e.code == 416:
                        tmp.unlink(missing_ok=True)
                        raise MineruError("HTTP 416：Range 越界，丢弃 .part 重来") from None
                    raise
                with resp:
                    if resp.status not in (200, 206):
                        raise MineruError(f"下载返回 HTTP {resp.status}")
                    cr = resp.headers.get("Content-Range", "")
                    if "/" in cr:
                        total = int(cr.rsplit("/", 1)[1])
                    elif resp.status == 200:
                        cl = resp.headers.get("Content-Length")
                        if cl and cl.isdigit():
                            total = int(cl)
                    if resp.status == 200 and got:
                        # 服务端忽略了 Range，只能从头来
                        tmp.unlink(missing_ok=True)
                        got = 0
                    with tmp.open("ab" if got else "wb") as f:
                        while True:
                            chunk = resp.read(1 << 20)
                            if not chunk:
                                break
                            f.write(chunk)
                            f.flush()          # 让磁盘长度实时跟上，供下一轮断点
                            got += len(chunk)
                if total is not None and got < total:
                    last = f"提前结束（{got}/{total} 字节）"
                elif expect_zip and not zipfile.is_zipfile(tmp):
                    # 字节数够了但不是 zip → 内容已错（多半是拼了陈旧 .part），丢弃重来
                    last = f"不是完整 zip（{got} 字节，缺中央目录），丢弃重下"
                    tmp.unlink(missing_ok=True)
                else:
                    os.replace(tmp, dest)      # 原子落位，绝不留半截
                    if attempt > 1:
                        self.log(f"[下载] 第 {attempt} 轮补齐完成（{got / 1048576:.1f} MB）")
                    return dest
            except Exception as e:             # noqa: BLE001
                last = f"{type(e).__name__}: {e}"
            if attempt >= retries:
                break
            done = tmp.stat().st_size if tmp.exists() else 0
            if done:
                # 已有进度 → 立刻用 Range 续，不空等
                self.log(f"[下载] 中断（{last}），已收 {done / 1048576:.1f} MB，断点续传 …")
            else:
                wait = min(30, 5 * 2 ** (attempt - 1))   # 5s / 10s / 20s
                self.log(f"[下载] 中断（{last}），{wait}s 后重试 …")
                time.sleep(wait)
        raise MineruError(f"下载失败（试了 {retries} 轮）：{last}  ← {url[:120]}")

    # ------------------------------------------------------------ 按 URL 单任务

    def submit_url(self, url: str) -> str:
        body = {
            "url": url,
            "is_ocr": bool(self.cfg.is_ocr),
            "enable_formula": bool(self.cfg.enable_formula),
            "language": self.cfg.language,
        }
        if self.cfg.model_version:
            body["model_version"] = self.cfg.model_version
        return self._api("/extract/task", "POST", body).get("task_id", "")

    def poll_task(self, task_id: str) -> TaskResult:
        deadline = time.time() + self.cfg.poll_timeout
        while True:
            d = self._api(f"/extract/task/{task_id}")
            r = TaskResult(file_name=d.get("file_name", ""), state=d.get("state", "unknown"),
                           full_zip_url=d.get("full_zip_url", "") or "",
                           err_msg=d.get("err_msg", "") or "")
            if r.done or r.failed:
                return r
            if time.time() > deadline:
                raise MineruError(f"任务 {task_id} 轮询超时")
            time.sleep(self.cfg.poll_interval)

    # ------------------------------------------------------------ 自检

    def probe(self) -> dict:
        """只读自检：token 是否可用、额度接口是否可达。不发上传申请，不消耗配额。"""
        out: dict = {"api_base": self.cfg.api_base, "ok": False}
        try:
            resp = self._request(self.cfg.api_base.rstrip("/") + "/extract-results/batch/__doclab_probe__")
            out["reachable"] = True
            out["code"] = resp.get("code")
            out["msg"] = resp.get("msg")
            # code=-60012「task not found or expire」= 鉴权已过、只是 id 不存在 → 说明 token 有效
            out["auth_ok"] = resp.get("code") in (-60012, 0, -60001, -60002)
            out["ok"] = bool(out["auth_ok"])
        except MineruError as e:
            out["reachable"] = "proxy-blocked" not in str(e)
            out["error"] = str(e)
        return out
