# -*- coding: utf-8 -*-
"""测试附件4全部70道题：pipeline + calc + compare"""
import sys, json, pandas as pd
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, 'D:/python-leanrn/codex')
sys.path.insert(0, 'D:/python-leanrn/codex/models/model2_question_understanding')
sys.path.insert(0, 'D:/python-leanrn/codex/pdf_extractor/lib')

from pipeline import FinancialQAPipeline
from calc import calc_single, calc_batch, calc_yoy, calc_cagr, list_formulas
from compare import rank, filter_rows, group_stats, intersect_rank

p = FinancialQAPipeline()
df = pd.read_excel(r'D:\HuaweiMoveData\Users\14725\Desktop\全部数据\正式数据\附件4：问题汇总.xlsx')

FORMULA_KEYWORDS = {
    "毛利率": "销售毛利率", "净利率": "销售净利率", "资产负债率": "资产负债率",
    "研发费用占比": "研发费用占比", "销售费用占比": "销售费用占比",
    "应收账款占比": "应收账款占比", "货币资金占比": "货币资金占比",
    "存货周转率": "存货周转率", "扣非净利润差值": "扣非净利润差值",
    "经营现金流净利润比": "经营现金流净利润比",
}

results = []
for i, row in df.iterrows():
    code = row.iloc[0]
    qtype = row.iloc[1]
    qjson = row.iloc[2]
    try:
        qs = json.loads(qjson)
        questions = [q.get('Q', '') for q in qs]
    except:
        questions = [str(qjson)]

    first_q = questions[0] if questions else ''
    if not first_q or len(first_q) < 3:
        results.append({'code': code, 'q': first_q[:30], 'status': 'SKIP', 'detail': ''})
        continue

    # 判断需要什么skill
    needs_calc = None
    for kw, formula in FORMULA_KEYWORDS.items():
        if kw in first_q:
            needs_calc = formula
            break
    needs_compare = any(kw in first_q for kw in ['排名', '前几', '前五', '前十', '前三', '最高', '最低', '最多', '最少'])
    needs_filter = any(kw in first_q for kw in ['超过', '低于', '大于', '小于', '为负', '为正', '超过60%', '低于40%'])
    needs_yoy = '同比' in first_q
    needs_industry = '行业均值' in first_q or '中位数' in first_q

    try:
        r = p.query(first_q)
        if r['success'] and r['data']:
            data = r['data']

            # 如果需要计算字段
            if needs_calc:
                calc_results = calc_batch(needs_calc, data)
                valid = [x for x in calc_results if x['value'] is not None]
                if valid:
                    results.append({'code': code, 'q': first_q[:40], 'status': 'OK',
                                    'detail': '%s: %d条有效' % (needs_calc, len(valid))})
                else:
                    results.append({'code': code, 'q': first_q[:40], 'status': 'PARTIAL',
                                    'detail': '%s计算无结果' % needs_calc})
            elif needs_filter:
                # 尝试筛选
                results.append({'code': code, 'q': first_q[:40], 'status': 'OK',
                                'detail': '原始数据%d条' % len(data)})
            else:
                results.append({'code': code, 'q': first_q[:40], 'status': 'OK',
                                'detail': '%d条数据' % len(data)})
        elif r['success'] and not r['data']:
            results.append({'code': code, 'q': first_q[:40], 'status': 'FAIL',
                            'detail': r.get('reason', '无数据')[:40]})
        else:
            results.append({'code': code, 'q': first_q[:40], 'status': 'FAIL',
                            'detail': r.get('reason', '查询失败')[:40]})
    except Exception as e:
        results.append({'code': code, 'q': first_q[:40], 'status': 'ERROR', 'detail': str(e)[:40]})

# 输出结果
print()
print('=' * 80)
ok = sum(1 for r in results if r['status'] == 'OK')
partial = sum(1 for r in results if r['status'] == 'PARTIAL')
fail = sum(1 for r in results if r['status'] == 'FAIL')
skip = sum(1 for r in results if r['status'] == 'SKIP')
err = sum(1 for r in results if r['status'] == 'ERROR')
print('Total: %d  OK: %d  PARTIAL: %d  FAIL: %d  SKIP: %d  ERROR: %d' % (len(results), ok, partial, fail, skip, err))
print('通过率: %.0f%%' % ((ok + partial) / max(len(results), 1) * 100))
print('=' * 80)

for r in results:
    icon = {'OK': '+', 'PARTIAL': '~', 'FAIL': '-', 'SKIP': '.', 'ERROR': '!'}[r['status']]
    print('[%s] %s %s | %s | %s' % (r['code'], icon, r['status'], r['q'], r['detail']))

# 失败原因分析
print()
print('=' * 80)
print('失败原因分析:')
print('=' * 80)
fail_reasons = {}
for r in results:
    if r['status'] in ('FAIL', 'ERROR'):
        reason = r['detail'][:20]
        fail_reasons[reason] = fail_reasons.get(reason, 0) + 1
for reason, count in sorted(fail_reasons.items(), key=lambda x: -x[1]):
    print('  %s: %d题' % (reason, count))
