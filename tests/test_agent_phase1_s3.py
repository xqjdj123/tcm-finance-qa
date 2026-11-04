# -*- coding: utf-8 -*-
"""T7 (S3) kg_resolver.SYNONYMS 与 field_dict.synonyms 去重 — 防回潮回归单测。

收敛方向（field_dict 为字段知识单一真相源）：
  kg_resolver.SYNONYMS 只允许保留 field_dict 没有的"补充同义词"；
  凡 key 已存在于 field_dict.synonyms（任一字段的 label/synonyms）者，
  一律视为重复，不得再进 kg_resolver.SYNONYMS —— 两处同义词集合必须不相交。

能力保持断言（去重不得砍能力）：
  - 同义词归一仍工作：真实别名经 field_dict 兜底归一（税后利润 → 净利润 → net_profit）
    与 kg 独有补充项归一（研发费 → 研发费用 → operating_expense_rnd_expenses）；
  - 层级展开仍工作：盈利能力 → [销售毛利率, 销售净利率, 加权平均净资产收益率]；
  - 模块可导入、单例可实例化。

运行（目标解释器）：
    python -m pytest tests/test_agent_phase1_s3.py -q
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 先导入 agent.taxonomy，让其把 models/model2_question_understanding 注入
# sys.path（与 kg_resolver 相同的真相源导入方式），再以同名模块导入 field_dict。
import agent.taxonomy  # noqa: F401
from field_dict import SCHEMA_DICT
from agent.kg_resolver import SYNONYMS, get_kg_resolver, HIERARCHY


def _field_dict_alias_set():
    """field_dict 已覆盖的别名全集：每字段 label + synonyms（label 即标准名可自解）。"""
    aliases = set()
    for info in SCHEMA_DICT.values():
        aliases.add(info["label"])
        aliases.update(info.get("synonyms", []))
    return aliases


_FD_ALIASES = _field_dict_alias_set()


class TestSynonymsDeduped(unittest.TestCase):
    """kg_resolver.SYNONYMS 与 field_dict.synonyms 去重（防回潮核心）。"""

    def test_no_synonyms_key_duplicated_in_field_dict(self):
        """kg_resolver.SYNONYMS 的任何 key 都不得出现在 field_dict label/synonyms 中。"""
        dup = sorted(set(SYNONYMS) & _FD_ALIASES)
        self.assertEqual(
            dup, [],
            "kg_resolver.SYNONYMS 与 field_dict 出现重复同义词（应删）：%s" % dup,
        )

    def test_synonyms_kept_are_real_supplements_only(self):
        """去重后保留项必须是 field_dict 没有的真实别名补充，且数量收敛。"""
        self.assertGreater(len(SYNONYMS), 0, "kg 独有补充项不应被全删")
        for alias in SYNONYMS:
            self.assertNotIn(alias, _FD_ALIASES,
                             "补充项误与 field_dict 冲突: %s" % alias)

    def test_field_dict_unchanged_as_truth_source(self):
        """field_dict 字段/别名结构仍完整：57 字段、每字段含 label/synonyms。"""
        self.assertGreaterEqual(len(SCHEMA_DICT), 57)
        for fname, info in SCHEMA_DICT.items():
            self.assertIn("label", info, fname)
            self.assertIn("synonyms", info, fname)


class TestKgResolverCapabilityKept(unittest.TestCase):
    """去重后 kg_resolver 归一/展开能力不得回退。"""

    def setUp(self):
        self.kg = get_kg_resolver()

    def test_synonym_normalize_via_field_dict_fallback(self):
        """field_dict 兜底归一：税后利润 → 净利润(DB label) → net_profit。"""
        r = self.kg.resolve(["税后利润"])
        self.assertEqual(r["indicators"], ["净利润"])
        self.assertEqual(r["indicators_en"], ["net_profit"])
        self.assertEqual(r["synonym_map"].get("税后利润"), "净利润")

    def test_synonym_normalize_kg_supplement(self):
        """kg 独有补充归一仍工作：研发费 → 研发费用 → 英文字段。"""
        r = self.kg.resolve(["研发费"])
        self.assertEqual(r["indicators"], ["研发费用"])
        self.assertEqual(r["indicators_en"], ["operating_expense_rnd_expenses"])

    def test_hierarchy_expansion_still_works(self):
        """盈利能力 → 3 个子指标中文 + 英文字段均可解析。"""
        self.assertIn("盈利能力", HIERARCHY)
        r = self.kg.resolve(["盈利能力"])
        self.assertEqual(
            r["indicators"],
            ["销售毛利率", "销售净利率", "加权平均净资产收益率"],
        )
        self.assertEqual(
            r["indicators_en"],
            ["gross_profit_margin", "net_profit_margin",
             "roe_weighted_excl_non_recurring"],
        )
        self.assertEqual(r["expanded"]["盈利能力"], r["indicators"])

    def test_mixed_resolve_keeps_dedup_behavior(self):
        """混合输入归一 + 展开结果与 field_dict 收敛口径一致。"""
        r = self.kg.resolve(["净利润", "营收"])
        self.assertEqual(r["indicators"], ["净利润", "营业总收入"])
        self.assertEqual(r["indicators_en"], ["net_profit", "total_operating_revenue"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
