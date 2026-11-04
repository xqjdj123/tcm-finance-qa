# -*- coding: utf-8 -*-
"""二阶段精排 + 检索层配置契约单测 — 全部 stub，不加载模型、不连服务。

钉住的契约：
  1. **N9 回归**：`domain/retriever/hybrid.py` 的 `_retriever_cfg()` 必须存在且
     返回必需键，`HybridRetriever()` 必须能实例化
     （缺陷：275f75c 起调用未定义符号 → rag_search 全程返回错误串）
  2. `resolve_rerank_mode()` 优先级：显式参数 > 环境变量 RAG_RERANK > config > off
  3. `CrossEncoderReranker.available()`：目录缺失/文件不全 → False
  4. **fail-open**：模型不可用或打分异常 → 原顺序返回（绝不阻断检索）
  5. 正常路径：按分数降序、保留 rrf_score、写 rerank_score
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.retriever.rerank import (CrossEncoderReranker,  # noqa: E402
                                    resolve_rerank_mode)


# ============================================================
# 1. N9 回归：检索层配置读取
# ============================================================
class TestRetrieverCfgN9(unittest.TestCase):
    """N9：`_retriever_cfg` 曾被调用却从未定义，导致整个混合检索器不可用。"""

    REQUIRED = ("rrf_k", "fusion_top_k", "company_boost",
                "candidate_multiplier", "channel_candidate_multiplier",
                "rerank", "rerank_pool_mult", "rerank_model_path")

    def test_retriever_cfg_exists_and_has_required_keys(self):
        from domain.retriever.hybrid import _retriever_cfg
        cfg = _retriever_cfg()
        self.assertIsInstance(cfg, dict)
        for k in self.REQUIRED:
            self.assertIn(k, cfg, "N9 回归：检索层配置缺键 %s" % k)

    def test_hybrid_retriever_instantiates(self):
        """修复前此处直接 NameError —— 这是 N9 的核心断言。"""
        from domain.retriever.hybrid import HybridRetriever
        r = HybridRetriever()
        self.assertGreater(r.COMPANY_BOOST, 0)
        self.assertGreaterEqual(r.CANDIDATE_MULTIPLIER, 1)
        self.assertGreaterEqual(r.RERANK_POOL_MULT, 1)

    def test_rerank_default_off(self):
        """默认必须不开精排（配置不变 ⇒ 行为不变）。"""
        from domain.retriever.hybrid import _retriever_cfg
        self.assertEqual(str(_retriever_cfg()["rerank"]).strip().lower(), "off")


# ============================================================
# 2. 开关优先级
# ============================================================
class TestResolveRerankMode(unittest.TestCase):

    def setUp(self):
        self._old = os.environ.get("RAG_RERANK")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("RAG_RERANK", None)
        else:
            os.environ["RAG_RERANK"] = self._old

    def test_explicit_wins_over_env(self):
        os.environ["RAG_RERANK"] = "off"
        self.assertEqual(resolve_rerank_mode("ce"), "ce")

    def test_explicit_off_wins_over_env_on(self):
        os.environ["RAG_RERANK"] = "1"
        self.assertEqual(resolve_rerank_mode("off"), "off")

    def test_env_used_when_explicit_none(self):
        os.environ["RAG_RERANK"] = "ce"
        self.assertEqual(resolve_rerank_mode(None), "ce")

    def test_env_alias_forms(self):
        for v in ("1", "true", "ON", " cross-encoder "):
            os.environ["RAG_RERANK"] = v
            self.assertEqual(resolve_rerank_mode(None), "ce", "别名未识别: %r" % v)

    def test_unknown_value_falls_through_to_config(self):
        os.environ["RAG_RERANK"] = "maybe"
        self.assertIn(resolve_rerank_mode(None), ("off", "ce"))


# ============================================================
# 3/4/5. 精排器：可用性、fail-open、正常排序
# ============================================================
class TestCrossEncoderReranker(unittest.TestCase):

    ITEMS = [
        {"text": "低分文档", "score": 0.9},
        {"text": "高分文档", "score": 0.1},
        {"text": "中分文档", "score": 0.5},
    ]

    def _stub(self):
        """构造一个指向不存在目录的实例（不会加载任何模型）。"""
        return CrossEncoderReranker(
            model_path=os.path.join(os.path.dirname(__file__), "_no_such_model_"))

    def test_available_false_when_dir_missing(self):
        self.assertFalse(self._stub().available())

    def test_score_returns_none_when_unavailable(self):
        self.assertIsNone(self._stub().score("q", ["a", "b"]))

    def test_rerank_fail_open_keeps_original_order(self):
        """模型不可用 → 原顺序返回，且截断到 top_k（绝不抛异常）。"""
        r = self._stub()
        out = r.rerank("q", list(self.ITEMS), top_k=2)
        self.assertEqual([x["text"] for x in out],
                         ["低分文档", "高分文档"])
        self.assertNotIn("rerank_score", out[0])

    def test_rerank_empty_items(self):
        self.assertEqual(self._stub().rerank("q", [], top_k=5), [])

    def test_rerank_orders_by_cross_encoder_score(self):
        """正常路径：按 CE 分降序，保留 rrf_score，写 rerank_score。"""
        r = self._stub()
        r.score = lambda q, ps: [0.1, 0.9, 0.5]      # 第 2 条最相关
        out = r.rerank("q", [dict(x) for x in self.ITEMS], top_k=3)
        self.assertEqual([x["text"] for x in out],
                         ["高分文档", "中分文档", "低分文档"])
        self.assertEqual(out[0]["rerank_score"], 0.9)
        self.assertEqual(out[0]["score"], 0.9)
        self.assertEqual(out[0]["rrf_score"], 0.1)   # 原分保留，便于追溯

    def test_rerank_score_length_mismatch_fail_open(self):
        r = self._stub()
        r.score = lambda q, ps: [0.5]                # 长度不匹配
        out = r.rerank("q", [dict(x) for x in self.ITEMS], top_k=3)
        self.assertEqual([x["text"] for x in out],
                         ["低分文档", "高分文档", "中分文档"])

    def test_rerank_truncates_to_top_k(self):
        r = self._stub()
        r.score = lambda q, ps: [0.1, 0.9, 0.5]
        out = r.rerank("q", [dict(x) for x in self.ITEMS], top_k=2)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["text"], "高分文档")


if __name__ == "__main__":
    unittest.main()
