# -*- coding: utf-8 -*-
"""D2 调用记忆与去重 — 两层指纹（args_fp 精确层 + result_fp 结果层）单测。

边界（v1.2）：引擎不解析自然语言，result_fp 的槽位只读工具返回体的
companies/indicators/years；query_type 不参与任何指纹。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.call_memory import CallMemory


class TestArgsFingerprint(unittest.TestCase):
    """参数指纹（精确层）：剔空值、键序不敏感、去空白、全角半角归一。"""

    def setUp(self):
        self.m = CallMemory()

    def test_ignores_empty_values_and_key_order(self):
        a = self.m.fingerprint(
            "sql_query",
            {"question": "白云山净利润", "query_type": None, "understanding": {}},
        )
        b = self.m.fingerprint(
            "sql_query",
            {"understanding": {}, "question": "白云山净利润", "query_type": ""},
        )
        self.assertEqual(a, b, "None/''/{} 空值应被剔除，键序不敏感")

    def test_fullwidth_halfwidth_and_space_normalized(self):
        a = self.m.fingerprint("sql_query", {"question": "白云山 2024"})
        b = self.m.fingerprint("sql_query", {"question": "白云山　２０２４"})
        self.assertEqual(a, b, "全角/半角/全角空格应归一为相同指纹")

    def test_different_args_differ(self):
        a = self.m.fingerprint("sql_query", {"question": "白云山"})
        b = self.m.fingerprint("sql_query", {"question": "云南白药"})
        self.assertNotEqual(a, b)

    def test_different_tool_differ(self):
        a = self.m.fingerprint("sql_query", {"question": "白云山"})
        b = self.m.fingerprint("chart", {"question": "白云山"})
        self.assertNotEqual(a, b, "工具名参与指纹，不同工具不串味")


class TestRecordLookupRetry(unittest.TestCase):
    """record / lookup 往返 + 失败重试语义。"""

    def setUp(self):
        self.m = CallMemory(max_retry=1)

    def _record(self, success=True):
        return self.m.record(
            "sql_query",
            {"question": "白云山净利润"},
            {"success": success, "data": [{"净利润": 100}] if success else []},
            '{"success": %s}' % ("true" if success else "false"),
            iteration=1,
        )

    def test_lookup_roundtrip(self):
        self.assertIsNone(self.m.lookup("sql_query", {"question": "白云山净利润"}))
        rec = self._record(success=True)
        got = self.m.lookup("sql_query", {"question": "白云山净利润"})
        self.assertIsNotNone(got)
        self.assertTrue(got.ok)
        self.assertEqual(got.result_content, rec.result_content)

    def test_retry_semantics_allow_one_retry(self):
        """首次失败 retries=0 → 允许再试；第 2 次失败 retries=1 → 拦截。"""
        r1 = self._record(success=False)
        self.assertEqual(r1.retries, 0, "首次失败 retries 应为 0")
        self.assertTrue(self.m.can_retry(r1), "失败后应允许重试 1 次")

        r2 = self._record(success=False)
        self.assertEqual(r2.retries, 1, "第 2 次失败 retries 应为 1")
        self.assertFalse(self.m.can_retry(r2), "重试次数用尽后应拦截")

    def test_success_after_failure_resets_ok(self):
        self._record(success=False)
        rec = self._record(success=True)
        self.assertTrue(rec.ok, "成功后 ok 应置真")
        self.assertTrue(self.m.has_success())

    def test_has_success_and_repeated_count(self):
        self.assertFalse(self.m.has_success())
        self._record(success=False)
        self.assertFalse(self.m.has_success(), "仅有失败记录不算有可用数据")
        self.m.mark_dup()
        self.assertEqual(self.m.repeated_count(), 1)


class TestResultFingerprint(unittest.TestCase):
    """结果指纹（结果层）：回答"这次调用有没有带来新信息"。"""

    def setUp(self):
        self.m = CallMemory()

    def _fp(self, **kw):
        body = {
            "success": True,
            "data": [{"v": 1}],
            "companies": kw.get("companies", []),
            "indicators": kw.get("indicators", []),
            "years": kw.get("years", []),
        }
        return self.m.result_fingerprint(body)

    def test_differs_by_indicator(self):
        fp1 = self._fp(companies=["云南白药"], indicators=["净利润"], years=["2024"])
        fp2 = self._fp(companies=["云南白药"], indicators=["净资产收益率"], years=["2024"])
        self.assertNotEqual(fp1, fp2, "不同指标应产生不同 result_fp")

    def test_same_slots_same_fp(self):
        fp1 = self._fp(companies=["云南白药"], indicators=["净利润"], years=["2024"])
        fp2 = self._fp(companies=["云南白药"], indicators=["净利润"], years=["2024"])
        self.assertEqual(fp1, fp2, "相同槽位应产生相同 result_fp（query_type 不入返回体）")

    def test_non_contract_tool_fallback(self):
        """无槽位字段但有 data 的非契约工具 → 退化哈希，仍非 None。"""
        fp = self.m.result_fingerprint({"success": True, "data": [{"a": 1}]})
        self.assertIsNotNone(fp)
        self.assertTrue(fp.startswith("fallback:"), fp)

    def test_no_data_returns_none(self):
        """无槽位且无 data → None（仅启用精确层）。"""
        self.assertIsNone(self.m.result_fingerprint({"success": True}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
