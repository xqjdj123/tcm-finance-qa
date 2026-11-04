# -*- coding: utf-8 -*-
"""D3 — 错误分类与恢复指引单测。

一期覆盖三类确定性失败：no_data / field_not_found / missing_param。
missing_param 指引已补（避免缺参白烧轮次）；timeout / db_error /
unknown 仍挂二期（指引返回空串）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.recovery import classify_error, render_guidance


class TestClassifyError(unittest.TestCase):

    def test_success_returns_empty(self):
        self.assertEqual(classify_error({"success": True}), "")
        self.assertEqual(classify_error({"success": True, "data": [{"a": 1}]}), "")

    def test_no_data(self):
        self.assertEqual(
            classify_error({"success": False, "error": "无数据", "data": []}),
            "no_data",
        )

    def test_field_not_found(self):
        self.assertEqual(
            classify_error({"success": False, "error": "字段不存在", "data": []}),
            "field_not_found",
        )

    def test_missing_param_classified_and_guided(self):
        """missing_param 能被 classify 识别，且一期已补指引（避免白烧轮次）。"""
        self.assertEqual(
            classify_error({"success": False, "error": "缺少参数 company"}),
            "missing_param",
        )
        g = render_guidance("missing_param")
        self.assertIn("参数", g)
        self.assertIn("company", g, "missing_param 指引应明确补 company 参数")

    def test_db_error_unknown(self):
        self.assertEqual(
            classify_error({"success": False, "error": "数据库连接超时"}),
            "unknown",
        )

    def test_non_dict_unknown(self):
        self.assertEqual(classify_error("oops"), "unknown")


class TestRenderGuidance(unittest.TestCase):

    def test_no_data_guidance(self):
        g = render_guidance("no_data")
        self.assertIn("无数据", g)
        self.assertIn("重查", g, "no_data 指引必须明确禁止同样条件重查")

    def test_field_not_found_guidance(self):
        g = render_guidance("field_not_found")
        self.assertIn("字段", g)
        self.assertIn("换用", g)

    def test_phase2_types_return_empty(self):
        for kind in ("timeout", "db_error", "unknown", ""):
            self.assertEqual(render_guidance(kind), "", f"{kind!r} 一期不应注入指引")


if __name__ == "__main__":
    unittest.main(verbosity=2)
