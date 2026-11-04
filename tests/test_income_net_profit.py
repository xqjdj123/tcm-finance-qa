# -*- coding: utf-8 -*-
"""返工16修5: 利润表 net_profit 标签完整性单测 — 防"顶格标签溢出列"回归。

背景: pdfplumber 把顶格(无缩进)加粗科目"五、净利润""1.归属于母公司股东的净利润"拆到
第0列、缩进科目拆到第1列，filter_sparse_columns 曾把稀疏的第0列当噪声删掉 → 顶格标签丢失、
net_profit 值在但标签空 → 无法匹配 → net_profit 缺失(万邦德 异号误判的根因之一)。
修5 新增 _merge_leading_label_column 在过滤稀疏列前把第0列合并进第1列。

本单测写死 万邦德 2024FY，断言 net_profit 必须提取到且值正确(合并口径 4524.82 万)，
并与 total_profit(5895.35 万)同号，防回归。

运行: python -m unittest tests.test_income_net_profit -v
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
WB_PDF = os.path.join(PDF_DIR, "reports-深交所", "万邦德：2024年年度报告.pdf")


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
class TestIncomeNetProfit(unittest.TestCase):

    def test_wb_2024fy_net_profit_correct(self):
        """万邦德 2024FY: net_profit=4524.82万(合并口径)，不得缺失/取母公司口径。"""
        self.assertTrue(os.path.exists(WB_PDF), f"样本 PDF 不存在: {WB_PDF}")
        best = extract_income_fields(WB_PDF, 2024, "FY")
        self.assertIsNotNone(best, "万邦德 income_sheet 未提取到")

        self.assertIn("net_profit", best, "万邦德 net_profit 缺失(五、净利润标签被丢)")
        self.assertIn("total_profit", best, "万邦德 total_profit 缺失")

        np_val, np_conf, np_src = best["net_profit"]
        tp_val = best["total_profit"][0]

        # 合并口径真值: 净利润 45,248,240.59 元 = 4524.8241 万
        self.assertAlmostEqual(np_val, 4524.8241, places=1,
                               msg=f"net_profit 值错({np_src}) 应为 4524.82 万")
        # 利润总额 58,953,507.97 元 = 5895.3508 万
        self.assertAlmostEqual(tp_val, 5895.3508, places=1,
                               msg=f"total_profit 值错 应为 5895.35 万")
        # 同号(异号=口径混淆 bug 已修)
        self.assertEqual((np_val > 0), (tp_val > 0),
                         f"异号残留: total_profit={tp_val} net_profit={np_val}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
