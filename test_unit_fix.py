# -*- coding: utf-8 -*-
"""验证单位修复：瑞康医药和康弘药业 2025Q3"""
import sys, os, json
sys.path.insert(0, "D:/python-leanrn/codex")
sys.path.insert(0, "D:/python-leanrn/codex/pdf_extractor/lib")
from pdf_extractor.file_parser import get_all_pdfs
from pdf_extractor.table_extractor import extract_all_tables
from pdf_extractor.column_recognizer import analyze
from pdf_extractor.field_mapper import match
from pdf_extractor.value_normalizer import normalize

# 找到目标PDF
all_pdfs = get_all_pdfs(r"D:\python-leanrn\codex\data\附件2：财务报告")
targets = [p for p in all_pdfs if p.get("name") and ("瑞康" in p["name"] or "康弘" in p["name"]) and p["yr"] == 2025 and p["rp"] == "Q3"]
if not targets:
    targets = [p for p in all_pdfs if ("瑞康" in p["fn"] or "康弘" in p["fn"]) and p["yr"] == 2025 and p["rp"] == "Q3"]

TBL_ORDER = ["income_sheet", "balance_sheet", "cash_flow_sheet", "core_performance_indicators_sheet"]

for p in targets:
    print("=" * 60)
    print("PDF: %s" % p["fn"])
    print("Company: %s  Year: %d  Period: %s" % (p["name"], p["yr"], p["rp"]))
    print("=" * 60)

    all_tables = extract_all_tables(p["fp"])

    for tt in TBL_ORDER:
        res = all_tables.get(tt)
        if not res or not res.get("matrix"):
            continue

        page = res["page"]
        uf = res.get("unit_factor", 1)
        print("\n  [%s] page=%d  unit_factor=%s" % (tt, page + 1, uf))

        ci = analyze(res["matrix"]["header_row"], p["yr"], p["rp"])
        tc = 1
        for c in ci:
            if c["type"] == "target":
                tc = c["index"]
                break

        count = 0
        for row in res["matrix"]["data_rows"]:
            if len(row) <= tc:
                continue
            label = row[0]
            entry, conf = match(label, context_tbl=tt)
            if not entry:
                continue
            raw_val = row[tc]
            norm_val = normalize(raw_val, uf, entry["f"])
            if norm_val is None:
                continue

            # 判断是否归一化正确：营收应该是几十亿级别（万元单位下是几十万）
            fname = entry["f"]
            if fname in ("total_operating_revenue", "net_profit", "eps"):
                if fname == "eps":
                    expected_range = (0.01, 100)  # EPS应该是几元
                else:
                    expected_range = (10000, 10000000)  # 营收/净利润应该是几十万万元(几十亿)
                tag = "[OK]" if expected_range[0] < abs(norm_val) < expected_range[1] else "[BAD]"
                print("    %-40s  raw=%15s  norm=%15.4f   %s" % (label, raw_val, norm_val, tag))
                count += 1
            if count >= 3:
                break
