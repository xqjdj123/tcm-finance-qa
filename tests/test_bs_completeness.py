# -*- coding: utf-8 -*-
"""返工16修4: BS 完整性单测 — 写死 PDF 路径 + 断言，防 BS 关键字段回归。

背景: 返工14/15 修好了 eps/roe，却把 BS 提取搞坏(equity/liability 缺失率恶化 5 倍)。
本单测写死 3 个样本，断言 BS 关键字段(asset_total_assets / liability_total_liabilities /
equity_total_equity) 必须提取到，防止再犯。

运行: python -m unittest tests.test_bs_completeness -v
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

# 返工16修4: 三个必测样本（写死路径）
BS_SAMPLES = {
    "600080_2023H1": (os.path.join(PDF_DIR, "reports-上交所", "600080_20230819_U8CH.pdf"), 2023, "H1"),
    "600129_2024FY": (os.path.join(PDF_DIR, "reports-上交所", "600129_20250329_NDLU.pdf"), 2024, "FY"),
    "600085_2023Q1": (os.path.join(PDF_DIR, "reports-上交所", "600085_20230429_VQ9H.pdf"), 2023, "Q1"),
}


def extract_bs_fields(pdf_path, year, rp):
    """复用提取链路，返回 BS 的 {字段: (值, conf, 来源标签)}，仅保留 conf>=60 的字段。"""
    r = extract_all_tables(pdf_path)
    bs = r.get("balance_sheet")
    if not bs or not bs.get("matrix"):
        return None
    m = bs["matrix"]
    ci = analyze(m["header_row"], year, rp)
    tc = 1
    for c in ci:
        if c["type"] == "target":
            tc = c["index"]
            break
    uf = bs.get("unit_factor", 1)
    best = {}
    for row in m["data_rows"]:
        if len(row) <= tc:
            continue
        entry, conf = match(row[0], context_tbl="balance_sheet")
        if not entry or conf < 60:
            continue
        v = normalize(row[tc], uf, entry["f"], label=row[0])
        if v is None:
            continue
        if entry["f"] not in best or conf > best[entry["f"]][1]:
            best[entry["f"]] = (v, conf, str(row[0]))
    return best


@unittest.skipUnless(os.path.isdir(PDF_DIR), "需要本地财报 PDF 样本（未随仓库分发）")
class TestBSCompleteness(unittest.TestCase):

    def _bs(self, name):
        path, year, rp = BS_SAMPLES[name]
        self.assertTrue(os.path.exists(path), f"样本 PDF 不存在: {path}")
        best = extract_bs_fields(path, year, rp)
        self.assertIsNotNone(best, f"{name}: balance_sheet 未提取到")
        return best

    def test_600080_bs_has_liability_and_equity(self):
        best = self._bs("600080_2023H1")
        self.assertIn("liability_total_liabilities", best,
                      "600080 BS 缺 liability_total_liabilities（负债合计）")
        self.assertIn("equity_total_equity", best,
                      "600080 BS 缺 equity_total_equity（所有者权益合计）")

    def test_600129_bs_has_liability(self):
        best = self._bs("600129_2024FY")
        self.assertIn("liability_total_liabilities", best,
                      "600129 BS 缺 liability_total_liabilities（负债合计）")
        self.assertIn("equity_total_equity", best,
                      "600129 BS 缺 equity_total_equity（所有者权益合计）")

    def test_600085_bs_has_equity(self):
        best = self._bs("600085_2023Q1")
        self.assertIn("equity_total_equity", best,
                      "600085 BS 缺 equity_total_equity（所有者权益合计）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
