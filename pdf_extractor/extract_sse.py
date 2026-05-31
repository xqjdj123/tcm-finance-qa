# -*- coding: utf-8 -*-
"""extract_sse.py: 上交所PDF提取 -> JSON (四表独立搜索+去重)"""
import sys, os, json, time
sys.path.insert(0, "D:/python-leanrn/codex")
sys.path.insert(0, "D:/python-leanrn/codex/pdf_extractor/lib")
from pdf_extractor.file_parser import get_all_pdfs
from pdf_extractor.table_extractor import extract_all_tables
from pdf_extractor.column_recognizer import analyze
from pdf_extractor.field_mapper import match
from pdf_extractor.value_normalizer import normalize
from pdf_extractor.confidence_scorer import P as score_p, F as score_f, V as score_v, C as score_c, calc as conf_calc, decide as conf_decide
from pdf_extractor.validator import validate_all, fmt_report, validation_score

OUT = "D:/python-leanrn/codex/data/extracted/sse"
os.makedirs(OUT, exist_ok=True)
all_pdfs = get_all_pdfs(r"D:\python-leanrn\codex\data\附件2：财务报告")
pdfs = [p for p in all_pdfs if p["ex"] == "SSE"]
pdfs.sort(key=lambda x: x["sz"])
print("上交所共 %d 个PDF" % len(pdfs))

TBL_CN = {"income_sheet":"利润表","balance_sheet":"资产负债表",
           "cash_flow_sheet":"现金流量表","core_performance_indicators_sheet":"核心业绩"}
TBL_ORDER = ["income_sheet","balance_sheet","cash_flow_sheet","core_performance_indicators_sheet"]
# M6: ????? - ???????
TBL_MIN_FIELDS = {
    "income_sheet": 4,
    "balance_sheet": 3,
    "cash_flow_sheet": 3,
    "core_performance_indicators_sheet": 2,
}


ok = fail = 0
total_fields = 0
total_checks = 0
total_passed = 0
start = time.time()
for idx, p in enumerate(pdfs):
    sc, yr, rp = p["code"], p["yr"], p["rp"]
    if not sc or not yr or not rp:
        fail += 1; continue

    fn_short = p["fn"][:40]
    all_tables = extract_all_tables(p["fp"])
    source = "pdfplumber_standard"
    merged_data = {}

    if all_tables:
        for tt in TBL_ORDER:
            res = all_tables.get(tt)
            if not res or not res.get("matrix"): continue

            sb = res["scores_breakdown"]
            total_score = res["score"]
            page_num = res["page"] + 1
            source_type = res.get("source_type", "statement")

            # 列识别
            ci = analyze(res["matrix"]["header_row"], yr, rp)
            tc = 1
            for c in ci:
                if c["type"] == "target": tc = c["index"]; break

            # 字段映射 + 去重：同一field取最高conf
            best_per_field = {}  # {field_name: (value, conf, orig_label)}
            for row in res["matrix"]["data_rows"]:
                if len(row) <= tc: continue
                label = row[0]
                entry, conf = match(label, context_tbl=tt)
                if not entry: continue
                from pdf_extractor.field_mapper import learn
                learn(label, entry["f"], conf)
                v = normalize(row[tc], res.get("unit_factor", 1), entry["f"])
                if v is None: continue
                fname = entry["f"]
                if fname not in best_per_field or conf > best_per_field[fname][1]:
                    best_per_field[fname] = (v, conf, label)

            if best_per_field:
                tbl_data = {f: v for f, (v, c, _) in best_per_field.items()}
                merged_data[tt] = tbl_data

                # 打印摘要
                print("  [%d/%d] %s  yr=%d rp=%s | %s(p%d) %s 评分%d | %d字段" % (
                    idx+1, len(pdfs), fn_short, yr, rp, TBL_CN[tt], page_num,
                    source_type.upper(), total_score, len(tbl_data)))
                total_fields += len(tbl_data)
                for fname, val in sorted(tbl_data.items(), key=lambda x: x[0]):
                    _, conf, orig = best_per_field[fname]
                    tag = "准确" if conf >= 80 else "模糊" if conf >= 60 else "存疑"
                    print("      %-40s = %12.4f  (←%s) conf=%.0f %s" % (fname, val, orig[:20], conf, tag))

    if not merged_data:
        print("  [%d/%d] %s  yr=%d rp=%s | FAIL" % (idx+1, len(pdfs), fn_short, yr, rp))
        fail += 1; continue

    # ===== 多表交叉填充 =====
    # 同一字段可能在多张表中出现，互相补充NULL值
    all_fields = {}
    for tt, tbl_data in merged_data.items():
        for fld, val in tbl_data.items():
            if fld not in all_fields:
                all_fields[fld] = (val, tt)
    cross_filled = 0
    for tt, tbl_data in merged_data.items():
        for fld, (val, src_tt) in all_fields.items():
            if fld not in tbl_data and src_tt != tt:
                tbl_data[fld] = val
                cross_filled += 1
    if cross_filled > 0:
        print("  [交叉填充] 补充了 %d 个字段" % cross_filled)

    # M6 validation (before JSON to include in output)
    v_pass, v_total, v_results = validate_all(merged_data)
    total_checks += v_total
    total_passed += v_pass
    v_score = validation_score(merged_data)
    print("  [M6校验] %d/%d 通过 | %s" % (
        v_pass, v_total,
        ", ".join("%s=%s" % (r["check"], "OK" if r["passed"] else "FAIL") for r in v_results)))
    if v_results:
        for r in v_results:
            if not r["passed"]:
                print("    XX %s: %s" % (r["check"], r["detail"]))

    fname = "%s_%s%s_%s.json" % (sc, yr, rp, p["type"])
    json.dump({"stock_code":sc,"stock_abbr":p.get("name") or sc,"report_year":yr,
        "report_period":rp,"report_type":p["type"],"exchange":"SSE",
        "source":source,"confidence":80,
        "validation":{"pass":v_pass,"total":v_total,"score":v_score,"details":v_results},
        "data":merged_data},
        open(os.path.join(OUT,fname),"w",encoding="utf-8"),ensure_ascii=False,indent=2)
    ok += 1

    if (idx+1) % 50 == 0:
        print("  --- 进度: [%d/%d] %ds ok=%d fail=%d 字段=%d ---" % (idx+1, len(pdfs), time.time()-start, ok, fail, total_fields))

print("\n完成! %ds ok=%d fail=%d 字段=%d" % (time.time()-start, ok, fail, total_fields))
print("M6: %d/%d (%.0f%%)" % (total_passed, max(total_checks,1), total_passed/max(total_checks,1)*100))
