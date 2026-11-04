# -*- coding: utf-8 -*-
"""D1 收敛判据（规则层）— 三态单测：enough_data 软提示不强制 /
result_fp 打转收口 / soft_limit 收口。引擎不解析自然语言，充分性只读
工具返回体（has_data）与 CallMemory（has_success）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.evaluator import Evaluator


class _Memory:
    """最小 CallMemory 替身：Evaluator 只调用 has_success()。"""

    def __init__(self, has_success):
        self._has_success = has_success

    def has_success(self):
        return self._has_success


class TestEvaluator(unittest.TestCase):

    def test_enough_data_soft_only(self):
        """有数据 → sufficient=True 但 force_answer=False（多公司/多指标可能只查回一部分）。"""
        ev = Evaluator(soft_iteration_limit=5, max_iterations=8)
        e = ev.evaluate(
            "q", None,
            {"has_data": True, "all_dup": False, "last_result_fp": "fp1"}, 1,
        )
        self.assertTrue(e.sufficient)
        self.assertFalse(e.force_answer)
        self.assertEqual(e.reason, "enough_data")

    def test_result_fp_unchanged_forces_answer(self):
        """连续两轮 result_fp 未变（打转）→ 强制收口。"""
        ev = Evaluator(soft_iteration_limit=5, max_iterations=8)
        ev.evaluate("q", None,
                    {"has_data": True, "all_dup": False, "last_result_fp": "fp1"}, 1)
        e = ev.evaluate("q", None,
                        {"has_data": True, "all_dup": False, "last_result_fp": "fp1"}, 2)
        self.assertTrue(e.force_answer)
        self.assertEqual(e.reason, "no_new_info")

    def test_all_dup_forces_answer(self):
        """本轮全部工具调用被拦截 → 无新信息 → 强制收口。"""
        ev = Evaluator()
        e = ev.evaluate("q", None,
                        {"has_data": False, "all_dup": True, "last_result_fp": None}, 3)
        self.assertTrue(e.force_answer)
        self.assertEqual(e.reason, "no_new_info")

    def test_soft_limit_forces_answer(self):
        """到软上限且已有可用数据 → 强制收口。"""
        ev = Evaluator(soft_iteration_limit=5, max_iterations=8)
        e = ev.evaluate("q", _Memory(True),
                        {"has_data": False, "all_dup": False, "last_result_fp": "x"}, 6)
        self.assertTrue(e.force_answer)
        self.assertEqual(e.reason, "soft_limit")

    def test_need_more_before_soft_limit(self):
        """早期无数据 → 继续，不强制。"""
        ev = Evaluator(soft_iteration_limit=5, max_iterations=8)
        e = ev.evaluate("q", _Memory(False),
                        {"has_data": False, "all_dup": False, "last_result_fp": "fp1"}, 2)
        self.assertFalse(e.sufficient)
        self.assertFalse(e.force_answer)
        self.assertEqual(e.reason, "need_more")


if __name__ == "__main__":
    unittest.main(verbosity=2)
