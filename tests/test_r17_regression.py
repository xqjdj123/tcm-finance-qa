# -*- coding: utf-8 -*-
"""返工17 修4: 回归单测 — 锁死修1/修2/修3 的四处修复，防再次回归。

覆盖:
  修1 报告期映射: 4月底披露的年报(片仔癀 600436_20250426)必须判 FY(2024)，不能判 Q1。
  修2 摘要版合并表: 摘要版(600479 UQ0R)不提取三主表; 全文版(P8TI)营收取合并口径(18.18亿)。
  修3 营收取错列: "营业外收入"不得映射 total_operating_revenue; 云南白药 2024FY 营收=400.33亿。

运行: python -m unittest tests.test_r17_regression -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdf_extractor.table_extractor import extract_all_tables
from pdf_extractor.column_recognizer import analyze
from pdf_extractor.field_mapper import match
from pdf_extractor.value_normalizer import normalize

PDF_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "附件2：财务报告")
SSE_DIR = os.path.join(PDF_DIR, "reports-上交所")
SZSE_DIR = os.path.join(PDF_DIR, "reports-深交所")

# 修1 样本: 片仔癀 2024年报(2025-04-26 披露)
PZH_FY24 = os.path.join(SSE_DIR, "600436_20250426_YT7B.pdf")
# 修2 样本: 千金药业 2025H1 摘要版 + 全文版
QJ_SUMMARY = os.path.join(SSE_DIR, "600479_20250830_UQ0R.pdf")
QJ_FULL = os.path.join(SSE_DIR, "600479_20250830_P8TI.pdf")
# 修3 样本: 云南白药 2024年报
YNBY_FY24 = os.path.join(SZSE_DIR, "云南白药：2024年年度报告.pdf")
# 修3 跨页合并样本: 云南白药 2024Q1/2024H1(首页9列有表头 + 续表无表头3列，值列错位)
YNBY_Q1 = os.path.join(SZSE_DIR, "云南白药：2024年一季度报告.pdf")
YNBY_H1 = os.path.join(SZSE_DIR, "云南白药：2024年半年度报告.pdf")


def extract_income_fields(pdf_path, year, rp):
    """复用提取链路，返回利润表 {字段: (值, conf, 来源标签)}，仅保留 conf>=60。"""
    r = extract_all_tables(pdf_path)
    isheet = r.get("income_sheet")
    if not isheet or not isheet.get("matrix"):
        return None
    m = isheet["matrix"]
    ci = analyze(m["header_row"], year, rp)
    tc = 1
    for c in ci:
        if c["type"] == "target":
            tc = c["index"]
            break
    uf = isheet.get("unit_factor", 1)
    best = {}
    for row in m["data_rows"]:
        if len(row) <= tc:
            continue
        entry, conf = match(row[0], context_tbl="income_sheet")
        if not entry or conf < 60:
            continue
        v = normalize(row[tc], uf, entry["f"], label=row[0])
        if v is None:
            continue
        if entry["f"] not in best or conf > best[entry["f"]][1]:
            best[entry["f"]] = (v, conf, str(row[0]))
    return best


@unittest.skipUnless(os.path.isdir(PDF_DIR), "需要本地财报 PDF 样本（未随仓库分发）")
class TestR17PeriodMapping(unittest.TestCase):
    """修1: 边界月份(4/10月)报告期必须内容核实，4月底年报≠Q1。"""

    def test_pzh_april_annual_is_fy_2024(self):
        from pdf_extractor.file_parser import (
            parse_sse, _sse_filename_year_month, _verify_period_by_content, BOUNDARY_MONTHS,
        )
        fn = "600436_20250426_YT7B.pdf"
        self.assertTrue(os.path.exists(PZH_FY24), f"样本不存在: {PZH_FY24}")

        code, yr, rp = parse_sse(fn)
        y, m = _sse_filename_year_month(fn)
        # 文件名月份=4，属于边界月，必须走内容核实
        self.assertIn(m, BOUNDARY_MONTHS, "4月应触发内容核实")
        self.assertEqual(parse_sse(fn)[2], "Q1", "文件名解析仍会误判 Q1(这正是修1要修的)")

        vr, cy = _verify_period_by_content(PZH_FY24)
        self.assertEqual(vr, "FY", f"内容核实应判 FY，实际 {vr}")
        self.assertEqual(cy, 2024, f"年报年份应为 2024，实际 {cy}")

        # 复刻 get_all_pdfs 的修1 修正逻辑，断言最终 (yr, rp) = (2024, FY)
        if m in BOUNDARY_MONTHS:
            if vr is not None:
                rp = vr
                if cy is not None:
                    yr = cy
                elif vr == "FY" and y is not None and m <= 4:
                    yr = y - 1
        self.assertEqual((yr, rp), (2024, "FY"),
                         f"修1后应映射到 (2024, FY)，实际 ({yr}, {rp})")


@unittest.skipUnless(os.path.isdir(PDF_DIR), "需要本地财报 PDF 样本（未随仓库分发）")
class TestR17SummaryConsolidated(unittest.TestCase):
    """修2: 摘要版不提取三主表; 全文版营收取合并口径。"""

    def test_summary_skips_main_tables(self):
        self.assertTrue(os.path.exists(QJ_SUMMARY), f"样本不存在: {QJ_SUMMARY}")
        r = extract_all_tables(QJ_SUMMARY)
        for tt in ("income_sheet", "balance_sheet", "cash_flow_sheet"):
            self.assertNotIn(tt, r, f"摘要版不应提取 {tt}")

    def test_full_consolidated_revenue(self):
        """全文版 600479 营收=18.18亿(合并口径)，不得取母公司口径 4.88亿。"""
        self.assertTrue(os.path.exists(QJ_FULL), f"样本不存在: {QJ_FULL}")
        best = extract_income_fields(QJ_FULL, 2025, "H1")
        self.assertIsNotNone(best, "600479 全文版 income_sheet 未提取到")
        self.assertIn("total_operating_revenue", best, "600479 营收缺失")

        rev = best["total_operating_revenue"][0]
        # 正确: 1,817,998,691.39 元 = 181,799.87 万(合并)
        self.assertAlmostEqual(rev, 181799.87, delta=500,
                               msg=f"营收应约 18.18 亿，实际 {rev} 万")
        # 错误值 48,829 万(母公司口径) 必须被排除
        self.assertGreater(rev, 100000, "营收应>10亿，不得取母公司口径 4.88亿")


@unittest.skipUnless(os.path.isdir(PDF_DIR), "需要本地财报 PDF 样本（未随仓库分发）")
class TestR17RevenueColumn(unittest.TestCase):
    """修3: 营业外收入不得映射营收; 云南白药营收取对列。"""

    def test_non_operating_income_not_revenue(self):
        for lbl in ("营业外收入", "加：营业外收入"):
            entry, conf = match(lbl, context_tbl="income_sheet")
            self.assertTrue(
                entry is None or entry.get("f") != "total_operating_revenue",
                f"{lbl} 错误映射到 total_operating_revenue(conf={conf})",
            )
        # 正向: 营业总收入 必须能映射到营收
        entry, conf = match("营业总收入", context_tbl="income_sheet")
        self.assertIsNotNone(entry, "营业总收入 未匹配到任何字段")
        self.assertEqual(entry["f"], "total_operating_revenue", "营业总收入 应映射营收")

    def test_ynby_2024fy_revenue_correct(self):
        """云南白药 2024FY 营收=400.33亿，不得取营业外收入 1548 万。"""
        self.assertTrue(os.path.exists(YNBY_FY24), f"样本不存在: {YNBY_FY24}")
        best = extract_income_fields(YNBY_FY24, 2024, "FY")
        self.assertIsNotNone(best, "云南白药 income_sheet 未提取到")
        self.assertIn("total_operating_revenue", best, "云南白药 营收缺失")

        rev = best["total_operating_revenue"][0]
        # 正确: 40,033,300,814.72 元 = 4,003,330.08 万
        self.assertAlmostEqual(rev, 4003330.08, delta=100,
                               msg=f"营收应约 400.33 亿，实际 {rev} 万")
        # 错误值 1548.33 万(营业外收入) 必须被排除
        self.assertGreater(rev, 1000000, "营收应>100亿，不得取营业外收入 1548 万")


@unittest.skipUnless(os.path.isdir(PDF_DIR), "需要本地财报 PDF 样本（未随仓库分发）")
class TestR17CrossPageMerge(unittest.TestCase):
    """修3: 跨页合并时首页(9列有表头)与续表(无表头3列)值列错位，营收值不得丢失。"""

    def test_ynby_2024q1_revenue(self):
        """云南白药 2024Q1 营收=107.74亿(一、营业总收入)，不得取错列/丢值。"""
        self.assertTrue(os.path.exists(YNBY_Q1), f"样本不存在: {YNBY_Q1}")
        best = extract_income_fields(YNBY_Q1, 2024, "Q1")
        self.assertIsNotNone(best, "云南白药 2024Q1 income_sheet 未提取到")
        self.assertIn("total_operating_revenue", best, "云南白药 2024Q1 营收缺失")
        rev = best["total_operating_revenue"][0]
        # 正确: 10,774,290,921.49 元 = 1,077,429.09 万
        self.assertAlmostEqual(rev, 1077429.09, delta=100,
                               msg=f"2024Q1 营收应约 107.74 亿，实际 {rev} 万")
        self.assertGreater(rev, 100000, "营收应>10亿")

    def test_ynby_2024h1_revenue(self):
        """云南白药 2024H1 营收=204.55亿，不得取错列/丢值。"""
        self.assertTrue(os.path.exists(YNBY_H1), f"样本不存在: {YNBY_H1}")
        best = extract_income_fields(YNBY_H1, 2024, "H1")
        self.assertIsNotNone(best, "云南白药 2024H1 income_sheet 未提取到")
        self.assertIn("total_operating_revenue", best, "云南白药 2024H1 营收缺失")
        rev = best["total_operating_revenue"][0]
        # 正确: 20,455,286,287.52 元 = 2,045,528.63 万
        self.assertAlmostEqual(rev, 2045528.63, delta=100,
                               msg=f"2024H1 营收应约 204.55 亿，实际 {rev} 万")
        self.assertGreater(rev, 100000, "营收应>10亿")


if __name__ == "__main__":
    unittest.main(verbosity=2)
