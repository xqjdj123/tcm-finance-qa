import json, glob, sys
sys.path.insert(0, '.')
from pdf_extractor.validator import validate_all, validation_score
from collections import Counter, defaultdict

all_files = glob.glob('data/extracted/sse/*.json') + glob.glob('data/extracted/szse/*.json')

# ===== 1. 全局覆盖统计 =====
companies = set()
reports_by_co = defaultdict(list)
total_records = len(all_files)

# ===== 2. 字段提取完整性 =====
# 每张表期望字段 vs 实际提取字段
table_fields_present = defaultdict(lambda: defaultdict(int))  # table -> field -> count
table_fields_missing = defaultdict(lambda: defaultdict(int))  # table -> field -> count for files that have that table

# ===== 3. 校验通过率 =====
validation_stats = {'total_checks': 0, 'passed': 0, 'failed': 0}
pass_rates = []

# ===== 4. 数值异常检测 =====
NUMERIC_ANOMALIES = {
    'income_sheet': {},
    'balance_sheet': {},
    'cash_flow_sheet': {},
    'core_performance_indicators_sheet': {}
}

FIELD_RANGES = {
    'roe': (-0.5, 0.5),
    'roe_weighted_excl_non_recurring': (-0.5, 0.5),
    'gross_profit_margin': (-0.5, 1.0),
    'net_profit_margin': (-0.5, 1.0),
    'asset_liability_ratio': (0, 1.5),
    'eps': (-10, 100),
}

for fp in all_files:
    fn = fp.split('\\')[-1]
    with open(fp, 'r', encoding='utf-8') as f:
        rec = json.load(f)
    
    sc = rec.get('stock_code', '')
    abbr = rec.get('stock_abbr', '')
    yr = rec.get('report_year')
    rp = rec.get('report_period')
    rt = rec.get('report_type')
    
    companies.add(sc)
    key = (sc, abbr, yr, rp, rt)
    reports_by_co[abbr or sc].append(key)
    
    data = rec.get('data', {})
    tables_present = list(data.keys())
    
    # Validation
    if data:
        p, t, details = validate_all(data)
        validation_stats['total_checks'] += t
        validation_stats['passed'] += p
        validation_stats['failed'] += (t - p)
        if t > 0:
            pass_rates.append((p / t * 100, fn, abbr, yr, rp))
    
    # Field coverage
    for tbl, kv in data.items():
        for fld, val in kv.items():
            table_fields_present[tbl][fld] += 1
            # Check numeric anomalies
            if fld in FIELD_RANGES and isinstance(val, (int, float)):
                lo, hi = FIELD_RANGES[fld]
                if val < lo or val > hi:
                    if fn not in NUMERIC_ANOMALIES[tbl]:
                        NUMERIC_ANOMALIES[tbl][fn] = []
                    NUMERIC_ANOMALIES[tbl][fn].append((fld, val, lo, hi))

# ===== 报告 =====
print('=' * 60)
print('finance_data 数据库数据质量分析报告')
print('（基于1039个JSON提取文件推断）')
print('=' * 60)

# 1. 基础覆盖
print()
print('【1. 基础覆盖】')
print(f'  总JSON文件: {total_records}')
print(f'  覆盖公司数: {len(companies)}')
sse_count = len(glob.glob('data/extracted/sse/*.json'))
szse_count = len(glob.glob('data/extracted/szse/*.json'))
print(f'    上交所: {sse_count}个JSON, {len(glob.glob("data/extracted/sse/*.json")) // 18 if glob.glob("data/extracted/sse/*.json") else 0}家(估)')
print(f'    深交所: {szse_count}个JSON, {szse_count // 18}家(估)')

# 2. 字段提取完整率
print()
print('【2. 各表字段提取覆盖】')
SCHEMA_COLS = {
    'income_sheet': 20,
    'balance_sheet': 14,
    'cash_flow_sheet': 16,
    'core_performance_indicators_sheet': 20,
}
for tbl in ['income_sheet', 'balance_sheet', 'cash_flow_sheet', 'core_performance_indicators_sheet']:
    n_fields = len(table_fields_present[tbl])
    expected = SCHEMA_COLS.get(tbl, 0)
    total_files_with_tbl = sum(1 for fp in all_files if tbl in json.load(open(fp, 'r', encoding='utf-8')).get('data', {}))
    top_fields = sorted(table_fields_present[tbl].items(), key=lambda x: -x[1])[:5]
    print(f'  {tbl}:')
    print(f'    出现该表的文件数: {total_files_with_tbl}')
    print(f'    提取到不同字段数: {n_fields}/{expected}')
    print(f'    提取最多的字段: {[f"{f}({c})" for f,c in top_fields]}')

# 3. 校验通过率
print()
print('【3. M6交叉校验通过率】')
total_validated = validation_stats['passed'] + validation_stats['failed']
print(f'  总校验项: {validation_stats["total_checks"]}')
print(f'  通过: {validation_stats["passed"]} ({validation_stats["passed"]/max(validation_stats["total_checks"],1)*100:.1f}%)')
print(f'  失败: {validation_stats["failed"]}')
low_pass = [r for r in pass_rates if r[0] < 100]
if low_pass:
    print(f'  校验未全过的文件: {len(low_pass)}个')
    for rate, fn, abbr, yr, rp in sorted(low_pass)[:10]:
        print(f'    {fn}: {rate:.0f}%  ({abbr} {yr}_{rp})')

# 4. 数值异常
print()
print('【4. 数值异常检测】')
total_anomalies = sum(len(v) for tbl_d in NUMERIC_ANOMALIES.values() for v in tbl_d.values())
print(f'  发现 {total_anomalies} 个数值超出合理范围:')
for tbl, files in NUMERIC_ANOMALIES.items():
    if files:
        print(f'  [{tbl}]')
        for fn, items in list(files.items())[:5]:
            for fld, val, lo, hi in items:
                print(f'    {fn}: {fld}={val} (合理范围: {lo}~{hi})')
        if len(files) > 5:
            print(f'    ... 还有 {len(files)-5} 个文件')

# 5. 缺失重要字段的公司
print()
print('【5. 关键字段缺失统计】')
KEY_FIELDS = ['total_operating_revenue', 'net_profit', 'asset_total_assets', 'equity_total_equity', 'operating_cf_net_amount']
for fld in KEY_FIELDS:
    present_in = table_fields_present.get('income_sheet', {}).get(fld, 0) + table_fields_present.get('core_performance_indicators_sheet', {}).get(fld, 0)
    print(f'  {fld}: 出现在 {present_in}/{total_records} 个文件中')

# 6. SSE公司名问题
print()
print('【6. SSE公司名缺失】')
code_abbrs = set()
for fp in glob.glob('data/extracted/sse/*.json'):
    with open(fp, 'r', encoding='utf-8') as f:
        rec = json.load(f)
    code_abbrs.add(rec.get('stock_abbr', ''))
print(f'  {len(code_abbrs)}家上交所公司缺少中文名（stock_abbr为股票代码）')

print()
print('=' * 60)
print('分析完成')
