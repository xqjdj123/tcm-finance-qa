# -*- coding: utf-8 -*-
"""入库前全面质量检查：5项检查"""
import sys, os, json, glob, collections
sys.path.insert(0, "D:/python-leanrn/codex")

jfs = []
for d in ["D:/python-leanrn/codex/data/extracted/sse", "D:/python-leanrn/codex/data/extracted/szse"]:
    if os.path.isdir(d):
        jfs.extend(glob.glob(os.path.join(d, "*.json")))

# 加载所有数据
records = []
for fp in jfs:
    with open(fp, encoding="utf-8") as f:
        rec = json.load(f)
    rec["_fp"] = fp
    records.append(rec)

print("Loaded %d JSON files" % len(records))
print("=" * 70)

# ============================================================
# 检查1: 重复数据检测
# ============================================================
print("\n[CHECK 1] Duplicate detection: same company + year + period + table")
dup_count = 0
seen = collections.defaultdict(list)
for rec in records:
    sc = str(rec.get("stock_code", ""))
    yr = rec.get("report_year")
    rp = rec.get("report_period")
    for tbl in rec.get("data", {}):
        key = (sc, yr, rp, tbl)
        seen[key].append(os.path.basename(rec["_fp"]))

for key, files in seen.items():
    if len(files) > 1:
        dup_count += 1
        if dup_count <= 10:
            print("  DUP: %s %s %s %s -> %d files: %s" % (key[0], key[1], key[2], key[3], len(files), files[:3]))

print("  Total duplicates: %d" % dup_count)

# ============================================================
# 检查2: 时间序列连续性（累计值递增）
# ============================================================
print("\n[CHECK 2] Time series continuity (cumulative values should increase)")
print("  Checking revenue (total_operating_revenue) by company across periods...")

# 按公司+年份分组
company_year = collections.defaultdict(list)
for rec in records:
    sc = str(rec.get("stock_code", ""))
    yr = rec.get("report_year")
    rp = rec.get("report_period")
    inc = rec.get("data", {}).get("income_sheet", {})
    rev = inc.get("total_operating_revenue")
    if sc and yr and rev:
        company_year[(sc, yr)].append((rp, rev, os.path.basename(rec["_fp"])))

# 报告期排序
RP_ORDER = {"Q1": 1, "H1": 2, "Q3": 3, "FY": 4}
continuity_issues = 0
for (sc, yr), periods in sorted(company_year.items()):
    periods.sort(key=lambda x: RP_ORDER.get(x[0], 0))
    for i in range(1, len(periods)):
        prev_rp, prev_rev, prev_fn = periods[i-1]
        curr_rp, curr_rev, curr_fn = periods[i]
        if prev_rp in RP_ORDER and curr_rp in RP_ORDER:
            if RP_ORDER[curr_rp] > RP_ORDER[prev_rp]:
                # 累计值应该递增
                if curr_rev < prev_rev * 0.9:  # 允许10%容差
                    continuity_issues += 1
                    if continuity_issues <= 15:
                        print("  ISSUE: %s %s  %s(%.0f) > %s(%.0f)  ratio=%.2f  files: %s, %s" % (
                            sc, yr, prev_rp, prev_rev, curr_rp, curr_rev, prev_rev/max(curr_rev,1), prev_fn, curr_fn))

print("  Total continuity issues: %d" % continuity_issues)

# ============================================================
# 检查3: 同比变化率合理性
# ============================================================
print("\n[CHECK 3] YoY change rate reasonableness (|change| > 10x flagged)")

# 按公司+报告期分组，跨年比较
company_rp = collections.defaultdict(dict)
for rec in records:
    sc = str(rec.get("stock_code", ""))
    yr = rec.get("report_year")
    rp = rec.get("report_period")
    inc = rec.get("data", {}).get("income_sheet", {})
    rev = inc.get("total_operating_revenue")
    np = inc.get("net_profit")
    if sc and yr:
        company_rp[(sc, rp)][yr] = {"rev": rev, "np": np, "fn": os.path.basename(rec["_fp"])}

yoy_issues = 0
for (sc, rp), years in sorted(company_rp.items()):
    sorted_years = sorted(years.keys())
    for i in range(1, len(sorted_years)):
        prev_yr = sorted_years[i-1]
        curr_yr = sorted_years[i]
        if curr_yr - prev_yr > 2:
            continue  # 跳过非相邻年份
        for field in ["rev", "np"]:
            prev_val = years[prev_yr].get(field)
            curr_val = years[curr_yr].get(field)
            if prev_val and curr_val and abs(prev_val) > 100:
                change = abs((curr_val - prev_val) / prev_val)
                if change > 10:
                    yoy_issues += 1
                    if yoy_issues <= 15:
                        print("  ISSUE: %s %s  %s %d=%.0f -> %d=%.0f  change=%.1fx  %s" % (
                            sc, rp, field, prev_yr, prev_val, curr_yr, curr_val, change, years[curr_yr]["fn"]))

print("  Total YoY issues: %d" % yoy_issues)

# ============================================================
# 检查4: 字段内逻辑关系
# ============================================================
print("\n[CHECK 4] Field logic relationships")

logic_issues = 0
checks = [
    ("revenue >= profit", lambda d: (
        d.get("income_sheet", {}).get("total_operating_revenue"),
        d.get("income_sheet", {}).get("net_profit")
    ), lambda r, p: r >= p if (r and p and r > 0) else None),
    ("assets >= liabilities", lambda d: (
        d.get("balance_sheet", {}).get("asset_total_assets"),
        d.get("balance_sheet", {}).get("liability_total_liabilities")
    ), lambda a, l: a >= l if (a and l) else None),
]

for desc, getter, checker in checks:
    issues = 0
    for rec in records:
        data = rec.get("data", {})
        vals = getter(data)
        if vals[0] is not None and vals[1] is not None:
            result = checker(*vals)
            if result is False:
                issues += 1
                if issues <= 5:
                    print("  FAIL [%s]: %s  %s  v1=%.2f v2=%.2f" % (
                        desc, rec.get("stock_abbr",""), rec.get("report_year",""), vals[0], vals[1]))
    logic_issues += issues
    print("  %s: %d violations" % (desc, issues))

# ============================================================
# 检查5: 行业横向比对（3σ原则）
# ============================================================
print("\n[CHECK 5] Industry horizontal comparison (3-sigma)")

# 收集各字段的值
field_values = collections.defaultdict(list)
for rec in records:
    for tbl, kv in rec.get("data", {}).items():
        for fname, val in kv.items():
            if val is not None:
                field_values[fname].append((val, rec.get("stock_abbr",""), rec.get("report_year",""), os.path.basename(rec["_fp"])))

sigma_issues = 0
key_fields = ["total_operating_revenue", "net_profit", "eps", "roe_weighted_excl_non_recurring",
              "asset_total_assets", "gross_profit_margin", "net_profit_margin"]

for fname in key_fields:
    vals = field_values.get(fname, [])
    if len(vals) < 10:
        continue
    nums = [v[0] for v in vals]
    mean = sum(nums) / len(nums)
    variance = sum((x - mean) ** 2 for x in nums) / len(nums)
    std = variance ** 0.5
    if std == 0:
        continue

    outliers = [(v, name, yr, fn) for v, name, yr, fn in vals if abs(v - mean) > 3 * std]
    if outliers:
        sigma_issues += len(outliers)
        print("  %s: mean=%.2f std=%.2f  outliers=%d" % (fname, mean, std, len(outliers)))
        for v, name, yr, fn in outliers[:3]:
            print("    %s %s = %.2f  (z=%.1f)  %s" % (name, yr, v, (v-mean)/std, fn))

print("  Total 3-sigma outliers: %d" % sigma_issues)

# ============================================================
# 汇总
# ============================================================
print("\n" + "=" * 70)
print("SUMMARY")
print("  Total files: %d" % len(records))
print("  [1] Duplicates: %d" % dup_count)
print("  [2] Continuity issues: %d" % continuity_issues)
print("  [3] YoY anomalies: %d" % yoy_issues)
print("  [4] Logic violations: %d" % logic_issues)
print("  [5] 3-sigma outliers: %d" % sigma_issues)
print("=" * 70)
