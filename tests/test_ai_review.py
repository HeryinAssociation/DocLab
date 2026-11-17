"""内置 AI 目录核校的离线回归：不调用真实 API，模型响应全部注入。

覆盖：预览不落盘、严格校验（key/gid/title/evidence）、应用与备份、revision
过期拒绝、页码字符/序列依据、无依据页码拒绝、token 不回传。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import ai_review, intake
from core.config import AIConfig
from core.pagecal import load_calibration
from core.tocio import export_toc
from core.util import read_json, write_json


class AIReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ai-review-")
        self.root = Path(self.tmp.name)
        self.wd = self.root / "source"
        self.wd.mkdir()
        self.patches = [
            patch.object(ai_review, "work_dir", lambda sid: self.wd),
            patch.object(intake, "work_dir", lambda sid: self.wd),
        ]
        for p in self.patches:
            p.start()
        raw = [
            {"type": "text", "text": "封面文字", "page_idx": 0},
            {"type": "text", "text": "第一章 起点", "text_level": 1, "page_idx": 0},
            {"type": "text", "text": "第一段正文。", "page_idx": 0},
            {"type": "page_number", "text": "11", "page_idx": 0},
            {"type": "text", "text": "第一节 方法", "text_level": 2, "page_idx": 1},
            {"type": "text", "text": "第二段正文。", "page_idx": 1},
            {"type": "page_number", "text": "12", "page_idx": 1},
            {"type": "text", "text": "补充标题", "page_idx": 2},
            {"type": "text", "text": "第三节 材料", "text_level": 2, "page_idx": 3},
            {"type": "text", "text": "第三段正文。", "page_idx": 3},
            {"type": "text", "text": "第二章 终点", "text_level": 1, "page_idx": 4},
            {"type": "text", "text": "第四段正文。", "page_idx": 4},
        ]
        write_json(self.wd / "content_list.json", raw)
        write_json(self.wd / "project.json", {
            "source_id": "fixture", "doc_title": "AI 测试书",
            "source_file": str(self.wd / "book.pdf"),
            "shards": [{"tag": "P1", "project_dir": str(self.wd), "n_pages": 5}],
        })
        intake.recompute({"source_id": "fixture", "review_depth": 3})
        self.sid = "fixture"
        self.cfg = AIConfig(token="sk-test-only", base_url="https://example.invalid/v1",
                            model="fake-model")
        self.toc = export_toc(self.wd, self.sid, 3)
        self.by_title = {n["title"]: n for n in self.toc["nodes"]}

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def fake(self, data):
        return {"data": data, "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                        "total_tokens": 2},
                "model": "fake-model", "response_chars": 10}

    def preview(self, data):
        with patch.object(ai_review, "chat_json", return_value=self.fake(data)):
            return ai_review.preview(self.sid, 3, self.cfg)

    # ---------------------------------------------------------- 预览/应用
    def test_preview_does_not_write_manual_layer(self):
        node = self.by_title["第一节 方法"]
        p = self.preview({"review_note": "测试", "operations": [{
            "action": "level", "key": node["key"], "level": 3,
            "evidence": "测试：同一父章下两个节的层级应一致"}], "page_fixes": []})
        self.assertEqual(p["errors"], [])
        self.assertTrue(p["changes"])
        self.assertFalse((self.wd / "manual_edits.json").exists())
        self.assertTrue((self.wd / ai_review.PREVIEW_FILE).is_file())
        self.assertEqual(p["model"], "fake-model")

    def test_apply_level_writes_manual_layer_with_backup(self):
        node = self.by_title["第一节 方法"]
        p = self.preview({"operations": [{
            "action": "level", "key": node["key"], "level": 3,
            "evidence": "测试：同章内两个节应同层"}], "page_fixes": []})
        r = ai_review.apply_preview(self.sid, p["preview_id"], self.cfg)
        self.assertTrue(r["ok"])
        self.assertEqual(r["applied_operations"], 1)
        self.assertTrue(Path(r["backup"]).is_dir())
        self.assertTrue((self.wd / ai_review.REPORT_MD).is_file())
        out = read_json(self.wd / "outline.json", {})
        flat = []

        def walk(ns):
            for n in ns:
                flat.append(n)
                walk(n.get("children") or [])
        walk(out.get("tree") or [])
        got = next(n for n in flat if n.get("title") == "第一节 方法")
        self.assertEqual(got.get("level"), 3)
        self.assertEqual(ai_review.latest_preview(self.sid)["status"], "applied")

    def test_revision_change_after_preview_rejects_apply(self):
        node = self.by_title["第一节 方法"]
        p = self.preview({"operations": [{
            "action": "level", "key": node["key"], "level": 3,
            "evidence": "测试"}], "page_fixes": []})
        from core import manual
        manual.set_level(self.wd, node["key"], 2)
        with self.assertRaisesRegex(ai_review.AIError, "发生变化"):
            ai_review.apply_preview(self.sid, p["preview_id"], self.cfg)

    # ---------------------------------------------------------- 目录校验
    def test_add_requires_title_from_block_text(self):
        gid = next(b["gid"] for b in self.toc["nodes"] if b["title"] == "补充标题") \
            if any(b["title"] == "补充标题" for b in self.toc["nodes"]) else 6
        with self.assertRaisesRegex(ai_review.AIError, "不在块"):
            self.preview({"operations": [{
                "action": "add", "gid": gid, "title": "编造的标题", "level": 2,
                "evidence": "测试"}], "page_fixes": []})

    def test_delete_front_node_rejected(self):
        front = next(n for n in self.toc["nodes"] if n.get("marker") == "front")
        with self.assertRaisesRegex(ai_review.AIError, "前置"):
            self.preview({"operations": [{
                "action": "delete", "key": front["key"], "evidence": "测试"}],
                "page_fixes": []})

    def test_delete_parent_with_children_rejected(self):
        parent = self.by_title["第一章 起点"]
        with self.assertRaisesRegex(ai_review.AIError, "子节点"):
            self.preview({"operations": [{
                "action": "delete", "key": parent["key"], "evidence": "测试"}],
                "page_fixes": []})

    def test_unknown_key_rejected(self):
        with self.assertRaisesRegex(ai_review.AIError, "不在当前目录"):
            self.preview({"operations": [{
                "action": "level", "key": "999999", "level": 2,
                "evidence": "测试"}], "page_fixes": []})

    def test_missing_evidence_rejected(self):
        node = self.by_title["第一节 方法"]
        with self.assertRaisesRegex(ai_review.AIError, "evidence"):
            self.preview({"operations": [{
                "action": "level", "key": node["key"], "level": 3,
                "evidence": ""}], "page_fixes": []})

    # ---------------------------------------------------------- 页码
    def test_page_fix_with_sequence_support_applies(self):
        p = self.preview({"operations": [], "page_fixes": [{
            "shard": "P1", "page_idx": 2, "printed": 13,
            "evidence": "P1 页 1 原页码 12、页 3 原页码 14，页 2 夹在中间",
            "scope_evidence": "P1 页 2 至页 3 之前，按相邻观测步长 1"}]})
        self.assertEqual(p["errors"], [], p["errors"])
        self.assertEqual(len(p["anchors"]), 1)
        self.assertIn("连续序列", p["anchors"][0]["_support"])
        r = ai_review.apply_preview(self.sid, p["preview_id"], self.cfg)
        self.assertEqual(r["applied_anchors"], 1)
        cal = load_calibration(self.wd)
        self.assertEqual(cal.locator("P1", 2), "13")

    def test_apply_combines_toc_change_and_page_fix(self):
        node = self.by_title["第一节 方法"]
        p = self.preview({
            "operations": [{"action": "level", "key": node["key"], "level": 3,
                            "evidence": "测试：同章内两个节应同层"}],
            "page_fixes": [{"shard": "P1", "page_idx": 2, "printed": 13,
                            "evidence": "P1 页 1 为 12，序列补 13",
                            "scope_evidence": "P1 页 2 至页 3 之前"}]})
        self.assertEqual(p["errors"], [], p["errors"])
        r = ai_review.apply_preview(self.sid, p["preview_id"], self.cfg)
        self.assertEqual((r["applied_operations"], r["applied_anchors"]), (1, 1))
        self.assertEqual(load_calibration(self.wd).locator("P1", 2), "13")

    def test_page_fix_without_evidence_is_error_and_not_applied(self):
        p = self.preview({"operations": [], "page_fixes": [{
            "shard": "P1", "page_idx": 4, "printed": 99,
            "evidence": "猜的", "scope_evidence": "整页"}]})
        self.assertTrue(p["errors"])
        self.assertEqual(p["anchors"], [])
        with self.assertRaisesRegex(ai_review.AIError, "校验错误"):
            ai_review.apply_preview(self.sid, p["preview_id"], self.cfg)

    def test_page_fix_requires_scope_evidence(self):
        with self.assertRaisesRegex(ai_review.AIError, "scope_evidence"):
            self.preview({"operations": [], "page_fixes": [{
                "shard": "P1", "page_idx": 2, "printed": 13,
                "evidence": "P1 页 1 为 12、页 3 为 14"}]})

    def test_ocr_character_correction_supported(self):
        # 页 2 原文是容易被 OCR 混淆的「l3」，应能推断为 13
        raw = read_json(self.wd / "content_list.json", [])
        raw.append({"type": "page_number", "text": "l3", "page_idx": 2})
        write_json(self.wd / "content_list.json", raw)
        intake.recompute({"source_id": "fixture", "review_depth": 3})
        p = self.preview({"operations": [], "page_fixes": [{
            "shard": "P1", "page_idx": 2, "printed": 13,
            "evidence": "页 2 原文页码字符 l3，按序列应为 13",
            "scope_evidence": "P1 页 2 至页 3 之前"}]})
        self.assertEqual(p["errors"], [], p["errors"])
        self.assertEqual(len(p["anchors"]), 1)
        self.assertIn("原文页码字符", p["anchors"][0]["_support"])

    # ---------------------------------------------------------- 配置
    def test_public_config_never_returns_token(self):
        c = AIConfig(token="sk-1234567890abcdef")
        pub = c.public()
        self.assertNotIn("token", pub)
        self.assertNotIn("sk-1234567890abcdef", json.dumps(pub))
        self.assertTrue(pub["token_present"])
        self.assertTrue(pub["token_hint"].endswith("cdef"))

    def test_save_ai_token_roundtrip_and_clear(self):
        from core import config
        env = self.root / ".env.local"
        with patch.object(config, "ENV_FILE", env):
            config.save_ai_token("sk-test-token-1234")
            self.assertIn("DOCLAB_AI_TOKEN=sk-test-token-1234",
                          env.read_text(encoding="utf-8"))
            self.assertEqual(config.load_ai_config().token, "sk-test-token-1234")
            config.save_env_value("MINERU_TOKEN", "mineru-test")
            config.save_ai_token("")
            text = env.read_text(encoding="utf-8")
            self.assertNotIn("DOCLAB_AI_TOKEN", text)
            self.assertIn("MINERU_TOKEN=mineru-test", text)

    def test_chat_json_parses_fenced_json_and_usage(self):
        cfg = AIConfig(token="sk-test")
        resp = {"choices": [{"message": {"content": '```json\n{"ok": true}\n```'}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
                "model": "fake-model"}
        with patch.object(ai_review, "_post_json", return_value=(resp, {})) as post:
            r = ai_review.chat_json(cfg, "system", "user", max_tokens=10)
        self.assertEqual(r["data"], {"ok": True})
        self.assertEqual(r["usage"]["total_tokens"], 5)
        self.assertEqual(post.call_count, 1)

    def test_chat_json_requires_token(self):
        with self.assertRaisesRegex(ai_review.AIError, "token"):
            ai_review.chat_json(AIConfig(token=""), "s", "u")

    def test_chat_json_retries_without_response_format(self):
        cfg = AIConfig(token="sk-test")
        resp = {"choices": [{"message": {"content": '{"ok":true}'}}], "usage": {}}
        calls = {"n": 0}

        def fake_post(url, body, token, timeout):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ai_review.AIError("HTTP 400 response_format unsupported")
            return resp, {}

        with patch.object(ai_review, "_post_json", side_effect=fake_post):
            r = ai_review.chat_json(cfg, "s", "u")
        self.assertEqual(r["data"], {"ok": True})
        self.assertEqual(calls["n"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
