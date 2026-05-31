# -*- coding: utf-8 -*-
"""
Model 1 端到端测试：输入问题 → 匹配字段 → 生成SQL
"""
import json
import sys
import os

# 先把 inference.py 的路径加上
sys.path.insert(0, os.path.dirname(__file__))
from inference import FinancialSchemaMatcher

# 加载训练好的模型
matcher = FinancialSchemaMatcher("output")

# ============ 模拟的8道题（从你的比赛题目中选的） ============
questions = [
    # 基本查询
    "金花股份2023年利润总额是多少",

    # 统计分析
    "2025年前三季度收入超过200亿元的企业有哪些",

    # 多意图（含公司和指标的笛卡尔积）
    "999与白云山相比，去年收益率最高的是",

    # 意图模糊（需要推断"亏钱"=净利润为负）
    "哪些企业是亏钱的",

    # 时间趋势
    "千金药业近几年的利润总额变化趋势",

    # 条件查询
    "2025年第三季度研发费用占比前五的公司",

    # 融合查询（SQL + RAG）
    "结合中药创新药审批提速政策，2025年第三季度研发费用占比前五的中药公司",

    # 多步推理
    "2024年利润最高的top10企业是哪些",
]

# ============ SQL 模板 ============
# 表名映射
TABLE_MAP = {
    "core_performance_indicators_sheet": "业绩指标表",
    "income_sheet": "利润表",
    "balance_sheet": "资产负债表",
    "cash_flow_sheet": "现金流量表",
}


def generate_sql(question, matches):
    """
    根据匹配结果生成SQL（简易版，后续Model 2会替代这个逻辑）
    """
    if not matches:
        return "-- 未匹配到任何财务字段，无法生成SQL"

    # 取第一个匹配的字段作为主字段
    primary = list(matches.values())[0]
    col = primary["column_en"]
    table = primary["table_name"]

    # 判断是否包含公司名
    company = None
    for c in ["金花股份", "千金药业", "白云山", "999"]:
        if c in question:
            company = c
            break

    # 判断是否包含年份
    year = None
    for y in ["2023", "2024", "2025"]:
        if y in question:
            year = y
            break

    # 判断是否包含条件
    condition = None
    if "超过" in question or "大于" in question:
        # 简单提取数字条件
        import re
        nums = re.findall(r"\d+亿", question)
        if nums:
            val = nums[0].replace("亿", "")
            condition = f"> {val}0000"  # 万元单位

    # 组装SQL
    sql_parts = ["SELECT"]

    if "top" in question.lower() or "前" in question:
        sql_parts.append(f"  stock_abbr, {col}")
        sql_parts.append(f"FROM {table}")
        where = []
        if year:
            where.append(f"  report_year = {year}")
        if condition:
            where.append(f"  {col} {condition}")
        if year or condition:
            sql_parts.append("WHERE")
            sql_parts.append("\n  AND ".join(where))
        sql_parts.append(f"ORDER BY {col} DESC")
        sql_parts.append("LIMIT 10")
    elif company and year:
        sql_parts.append(f"  {col}")
        sql_parts.append(f"FROM {table}")
        sql_parts.append(f"WHERE stock_abbr = '{company}'")
        sql_parts.append(f"  AND report_year = {year}")
        sql_parts.append(f"  AND report_period = 'FY'")
    elif company:
        sql_parts.append(f"  report_year, {col}")
        sql_parts.append(f"FROM {table}")
        sql_parts.append(f"WHERE stock_abbr = '{company}'")
        sql_parts.append(f"  AND report_period = 'FY'")
        sql_parts.append("ORDER BY report_year")
    elif year:
        sql_parts.append(f"  stock_abbr, {col}")
        sql_parts.append(f"FROM {table}")
        sql_parts.append(f"WHERE report_year = {year}")
        sql_parts.append(f"  AND report_period = 'Q3'")
        if condition:
            sql_parts[-1] = sql_parts[-1] + f"\n  AND {col} {condition}"
    else:
        sql_parts.append(f"  stock_abbr, report_year, {col}")
        sql_parts.append(f"FROM {table}")
        sql_parts.append(f"WHERE {col} IS NOT NULL")
        sql_parts.append(f"  AND report_period = 'FY'")

    return "\n".join(sql_parts) + ";"


# ============ 测试执行 ============
print("=" * 70)
print("Model 1 端到端测试 — 8道题")
print("=" * 70)

for i, q in enumerate(questions, 1):
    print(f"\n{'─' * 70}")
    print(f"第{i}题: {q}")
    print(f"{'─' * 70}")

    # Step 1: Model 1 匹配字段
    matches = matcher.match_from_question(q)

    print("\n[Model 1 匹配结果]")
    if not matches:
        # 对于没有直接匹配到的，尝试模糊匹配关键词
        print("  (未精确匹配，尝试模糊匹配...)")
        # 手动提取关键词测试
        keywords = {
            "利润": "利润总额",
            "收入": "营业总收入",
            "研发": "研发费用",
            "亏": "净利润",
            "收益": "每股收益",
            "趋势": "营业总收入",
            "最高": "净利润",
            "占比": "研发费用",
        }
        for kw, term in keywords.items():
            if kw in q:
                result = matcher.match(term, top_k=1)
                if result:
                    matches[term] = result[0]
                    print(f"  模糊匹配: {term} → {result[0]['display']} (score: {result[0]['score']})")
                break
    else:
        for term, match in matches.items():
            print(f"  {term} → {match['display']} (score: {match['score']})")

    # Step 2: 生成SQL
    print("\n[生成的SQL]")
    sql = generate_sql(q, matches)
    print(f"  {sql}")

    # Step 3: 预期结果说明
    print("\n[预期说明]")
    if "利润总额" in q:
        print("  查 income_sheet.total_profit → 返回金花股份2023年利润总额")
    elif "趋势" in q:
        print("  查多年份的 total_operating_revenue → 返回趋势数据")
    elif "亏钱" in q:
        print("  查 net_profit_ltm < 0 的公司 → 返回亏损企业列表")
    elif "占比" in q:
        print("  查 rnd_ratio = rnd/revenue → 排序取前5")
    elif "最高" in q:
        print("  查 roe 或 net_profit → 比较999和白云山")
    elif "top" in q.lower() or "前" in q:
        print("  查净利润排名 → 返回TOP10")
