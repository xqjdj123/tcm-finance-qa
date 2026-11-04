# -*- coding: utf-8 -*-
"""T8 (A2) 可用指标知识注入 ReAct prompt 上下文 — 轻量回归单测。

验收点：
  1. ReAct 系统 Prompt 含"可用指标知识"块：收敛后的标准指标清单、
     归一示例（税后利润→净利润）、关键层级展开（盈利能力→其子指标）；
  2. 同义词全表（field_dict 256 + kg_resolver 77）不整表灌入：块体积
     恒 ≤ METRIC_KNOWLEDGE_MAX_CHARS，小上限下也按截断策略兜底；
  3. 字段引用零漂移：清单/示例/层级全部由 kg_resolver/field_dict 的
     只读导出现算，展示映射 == resolver 实测归一结果；
  4. 开关可关（include_metric_knowledge=False），YAML 缺失的兜底路径
     构造与注入均无异常，用户问题原文预处理路径未被动过（本任务只改
     prompt_manager / kg_resolver 导出层）。

运行（目标解释器）：
    python -m pytest tests/test_agent_phase1_a2.py -q
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 先导入 agent.kg_resolver，让其把 models/model2_question_understanding
# 注入 sys.path（与 taxonomy/kg_resolver 相同的真相源导入方式），再以同名
# 模块导入 field_dict —— 共享同一份 SCHEMA_DICT，避免出现两份漂移副本。
from agent.prompt_manager import (PromptManager,
                                  METRIC_KNOWLEDGE_MAX_CHARS)
import agent.kg_resolver as kg_resolver  # noqa: F401
from field_dict import SCHEMA_DICT


_HEADER = "## 可用指标知识"
_NORM_EXAMPLE = "税后利润→净利润"


class TestMetricKnowledgeInjection(unittest.TestCase):
    """指标知识块注入行为与体积控制。"""

    def setUp(self):
        self.pm = PromptManager()

    def test_system_prompt_contains_metric_knowledge(self):
        """ReAct 系统 Prompt 注入关键指标清单 / 归一示例 / 层级展开。"""
        prompt = self.pm.build_system_prompt(tool_descriptions="工具说明A")
        self.assertIn(_HEADER, prompt)
        self.assertIn("可用指标清单", prompt)
        self.assertIn(_NORM_EXAMPLE, prompt)
        self.assertIn(
            "盈利能力→[销售毛利率、销售净利率、加权平均净资产收益率]",
            prompt,
        )

    def test_default_block_volume_under_cap(self):
        """默认知识块体积不超上限且非空（有实质内容可读）。"""
        block = self.pm.build_metric_knowledge_block()
        self.assertGreater(len(block), 0)
        self.assertLessEqual(len(block), METRIC_KNOWLEDGE_MAX_CHARS)
        prompt = self.pm.build_system_prompt()
        self.assertIn(_HEADER, prompt)

    def test_block_respects_custom_cap(self):
        """自定义上限生效：返回值恒不超过上限；0/负上限等价于关闭。"""
        for cap in (150, 400, 800, 1200):
            with self.subTest(cap=cap):
                block = self.pm.build_metric_knowledge_block(max_chars=cap)
                self.assertLessEqual(len(block), cap)
                self.assertGreaterEqual(len(block), 0)
        self.assertEqual(self.pm.build_metric_knowledge_block(max_chars=0), "")
        self.assertEqual(self.pm.build_metric_knowledge_block(max_chars=-1), "")

    def test_toggle_off_disables_injection(self):
        """include_metric_knowledge=False 时整块不注入，默认开。"""
        on = self.pm.build_system_prompt()
        off = self.pm.build_system_prompt(include_metric_knowledge=False)
        self.assertIn(_HEADER, on)
        self.assertNotIn(_HEADER, off)
        self.assertNotIn(_NORM_EXAMPLE, off)

    def test_fallback_prompt_also_injects_and_no_exception(self):
        """YAML 缺失（兜底路径）同样注入知识块，构造过程无异常。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            fallback = PromptManager(config_dir=tmp_dir)
            prompt = fallback.build_system_prompt(
                tool_descriptions="T", extra_context="上一轮：白云山2024净利润")
            self.assertIn(_HEADER, prompt)
            self.assertIn(_NORM_EXAMPLE, prompt)


class TestKnowledgeZeroDrift(unittest.TestCase):
    """知识块内容与 kg_resolver/field_dict 单一真相源零漂移。"""

    def setUp(self):
        self.kg = kg_resolver.get_kg_resolver()
        self.labels = [info["label"] for info in SCHEMA_DICT.values()]
        self.label_set = set(self.labels)

    def test_indicator_list_is_field_dict_canonical_labels(self):
        """指标清单 = SCHEMA_DICT 全量中文标签（顺序一致、无重复）。"""
        actual = kg_resolver.standard_indicator_labels()
        self.assertEqual(actual, self.labels)
        self.assertGreaterEqual(len(actual), 57)
        self.assertEqual(len(actual), len(set(actual)))
        block = PromptManager().build_metric_knowledge_block()
        self.assertIn("可用指标清单（共%d个）：" % len(actual), block)
        # 渲染与数据快照同源：清单首尾标签与计数一致
        self.assertTrue(block.startswith(_HEADER))
        self.assertIn(self.labels[0], block)
        self.assertIn(self.labels[-1], block)

    def test_norm_examples_equal_resolver_ground_truth(self):
        """每条展示归一示例都等于 resolver 实测结果，且含验收示例。"""
        examples = kg_resolver.synonym_norm_examples()
        pairs = dict(examples)
        self.assertIn("税后利润", pairs)
        self.assertEqual(pairs["税后利润"], "净利润")
        self.assertLessEqual(len(examples), 8)
        for alias, canonical in examples:
            with self.subTest(alias=alias):
                result = self.kg.resolve([alias])
                self.assertEqual(result["indicators"], [canonical])
                self.assertIn(canonical, self.label_set)
                self.assertNotEqual(alias, canonical)

    def test_hierarchy_children_resolve_via_schema(self):
        """概念组展开的每个子指标都来自 SCHEMA_DICT 现算，且组集合不丢。"""
        expansions = kg_resolver.hierarchy_expansions()
        names = [name for name, _ in expansions]
        self.assertEqual(set(names), set(kg_resolver.HIERARCHY))
        self.assertIn("盈利能力", names)
        for name, children in expansions:
            with self.subTest(group=name):
                fields_en = kg_resolver.HIERARCHY[name]
                expected = [
                    SCHEMA_DICT[en]["label"]
                    for en in fields_en if en in SCHEMA_DICT
                ]
                self.assertEqual(children, expected)
                self.assertTrue(children)
                for child in children:
                    self.assertIn(child, self.label_set)

    def test_no_new_preprocessing_of_user_question(self):
        """A2 走 prompt 注入，不改用户问题原文预处理：解析入口仍是纯输入映射。"""
        # resolver 归一/展开能力保持：同一别名输入产出标准指标，原文不被改写
        result = self.kg.resolve(["税后利润"])
        self.assertEqual(result["indicators"], ["净利润"])
        self.assertEqual(result["synonym_map"], {"税后利润": "净利润"})
        # 知识块方法不接收用户问题参数，无法在系统侧改写原文（仅静态注入）
        self.assertNotIn("question", PromptManager.build_metric_knowledge_block.__code__.co_varnames)
        self.assertNotIn("question", PromptManager.build_system_prompt.__code__.co_varnames)


if __name__ == "__main__":
    unittest.main(verbosity=2)
