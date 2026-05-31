# -*- coding: utf-8 -*-
"""analysis.py: 有效性分析"""
import os, json, glob
from collections import Counter

dirs = ["D:/python-leanrn/codex/data/extracted/sse", "D:/python-leanrn/codex/data/extracted/szse"]
ANALYSIS = os.path.dirname(os.path.abspath(__file__))

report = {"extraction": {"total":0,"success":0,"failed":0,"sources":Counter(),"confidences":[]},
          "mapping": {"total":0,"mapped":0},
          "import_": {"companies":set(),"fields_per_company":Counter()}}

for d in dirs:
    if not os.path.isdir(d): continue
    for fp in glob.glob(os.path.join(d, "*.json")):
        report["extraction"]["total"] += 1
        with open(fp, encoding="utf-8") as f:
            try: rec = json.load(f)
            except: report["extraction"]["failed"] += 1; continue
        report["extraction"]["success"] += 1
        report["extraction"]["sources"][rec.get("source","unknown")] += 1
        report["extraction"]["confidences"].append(rec.get("confidence", 0))
        td = rec.get("data",{})
        for tbl, kv in td.items():
            report["mapping"]["total"] += len(kv)
            report["mapping"]["mapped"] += len(kv)
            report["import_"]["fields_per_company"][rec.get("stock_code","")] += len(kv)
        report["import_"]["companies"].add(rec.get("stock_code",""))

e = report["extraction"]
print("="*40)
print("有效性分析报告")
print("="*40)
print("\n-- 抽取结果 --")
print("  总PDF: %d" % e["total"])
print("  成功: %d (%.1f%%)" % (e["success"], e["success"]/max(e["total"],1)*100))
print("  失败: %d" % e["failed"])
for src, cnt in e["sources"].most_common():
    print("    %s: %d" % (src, cnt))
if e["confidences"]:
    avg = sum(e["confidences"])/len(e["confidences"])
    h = sum(1 for c in e["confidences"] if c >= 80)
    m = sum(1 for c in e["confidences"] if 60 <= c < 80)
    l = sum(1 for c in e["confidences"] if c < 60)
    print("  平均置信度: %.1f" % avg)
    print("  分布: >=80=%d  60-80=%d  <60=%d" % (h,m,l))
print("\n-- 映射结果 --")
print("  总字段: %d" % report["mapping"]["total"])
print("-- 入库结果 --")
print("  覆盖公司: %d" % len(report["import_"]["companies"]))
avg_f = sum(report["import_"]["fields_per_company"].values())/max(len(report["import_"]["fields_per_company"]),1)
print("  平均字段/公司: %.1f" % avg_f)
json.dump({"extraction":dict(e),"mapping":report["mapping"],
    "import":{"companies":len(report["import_"]["companies"]),
    "avg_fields":round(avg_f,1)}},
    open(os.path.join(ANALYSIS,"analysis_report.json"),"w",encoding="utf-8"),ensure_ascii=False,indent=2)
print("\n报告已保存到:", os.path.join(ANALYSIS,"analysis_report.json"))
