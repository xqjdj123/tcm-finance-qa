# -*- coding: utf-8 -*-
"""calc.py: 运行时计算派生指标
接收sql_query返回的基础数据 + 公式名，返回计算结果。
不直接查数据库，只做数学运算。
"""

# ===== 公式字典 =====
# 每个公式定义：需要的字段、计算函数、单位、描述
FORMULAS = {
    # --- 占比类 ---
    "销售毛利率": {
        "fields": ["total_operating_revenue", "operating_expense_cost_of_sales"],
        "formula": "(营收-营业成本)/营收*100",
        "unit": "%",
        "calc": lambda d: _safe_pct(_sub(d, "total_operating_revenue", "operating_expense_cost_of_sales"), d.get("total_operating_revenue")),
    },
    "销售净利率": {
        "fields": ["total_operating_revenue", "net_profit"],
        "formula": "净利润/营收*100",
        "unit": "%",
        "calc": lambda d: _safe_pct(d.get("net_profit"), d.get("total_operating_revenue")),
    },
    "资产负债率": {
        "fields": ["liability_total_liabilities", "asset_total_assets"],
        "formula": "负债总额/资产总额*100",
        "unit": "%",
        "calc": lambda d: _safe_pct(d.get("liability_total_liabilities"), d.get("asset_total_assets")),
    },
    "研发费用占比": {
        "fields": ["operating_expense_rnd_expenses", "total_operating_revenue"],
        "formula": "研发费用/营收*100",
        "unit": "%",
        "calc": lambda d: _safe_pct(d.get("operating_expense_rnd_expenses"), d.get("total_operating_revenue")),
    },
    "销售费用占比": {
        "fields": ["operating_expense_selling_expenses", "total_operating_revenue"],
        "formula": "销售费用/营收*100",
        "unit": "%",
        "calc": lambda d: _safe_pct(d.get("operating_expense_selling_expenses"), d.get("total_operating_revenue")),
    },
    "应收账款占比": {
        "fields": ["asset_accounts_receivable", "total_operating_revenue"],
        "formula": "应收账款/营收*100",
        "unit": "%",
        "calc": lambda d: _safe_pct(d.get("asset_accounts_receivable"), d.get("total_operating_revenue")),
    },
    "货币资金占比": {
        "fields": ["asset_cash_and_cash_equivalents", "asset_total_assets"],
        "formula": "货币资金/总资产*100",
        "unit": "%",
        "calc": lambda d: _safe_pct(d.get("asset_cash_and_cash_equivalents"), d.get("asset_total_assets")),
    },
    "净利润占未分配利润比": {
        "fields": ["net_profit", "equity_unappropriated_profit"],
        "formula": "净利润/未分配利润*100",
        "unit": "%",
        "calc": lambda d: _safe_pct(d.get("net_profit"), d.get("equity_unappropriated_profit")),
    },
    "扣非净利润差值": {
        "fields": ["net_profit", "net_profit_excl_non_recurring"],
        "formula": "|净利润-扣非净利润|",
        "unit": "万元",
        "calc": lambda d: _safe_abs_diff(d.get("net_profit"), d.get("net_profit_excl_non_recurring")),
    },

    # --- 比率类 ---
    "存货周转率": {
        "fields": ["operating_expense_cost_of_sales", "asset_inventory"],
        "formula": "营业成本/存货",
        "unit": "次",
        "calc": lambda d: _safe_ratio(d.get("operating_expense_cost_of_sales"), d.get("asset_inventory")),
    },
    "经营现金流净利润比": {
        "fields": ["operating_cf_net_amount", "net_profit"],
        "formula": "经营现金流/净利润",
        "unit": "",
        "calc": lambda d: _safe_ratio(d.get("operating_cf_net_amount"), d.get("net_profit")),
    },

    # --- 差值类 ---
    "短期借款超货币资金": {
        "fields": ["liability_short_term_loans", "asset_cash_and_cash_equivalents"],
        "formula": "短期借款-货币资金",
        "unit": "万元",
        "calc": lambda d: _safe_sub_val(d.get("liability_short_term_loans"), d.get("asset_cash_and_cash_equivalents")),
    },
    "资产减存货": {
        "fields": ["asset_total_assets", "asset_inventory"],
        "formula": "总资产-存货",
        "unit": "万元",
        "calc": lambda d: _safe_sub_val(d.get("asset_total_assets"), d.get("asset_inventory")),
    },
}


def _safe_pct(numerator, denominator):
    """安全百分比计算"""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round(float(numerator) / float(denominator) * 100, 4)


def _safe_ratio(numerator, denominator):
    """安全比值计算"""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round(float(numerator) / float(denominator), 4)


def _safe_sub_val(a, b):
    """安全差值计算"""
    if a is None or b is None:
        return None
    return round(float(a) - float(b), 4)


def _safe_abs_diff(a, b):
    """安全绝对差值"""
    if a is None or b is None:
        return None
    return round(abs(float(a) - float(b)), 4)


def _sub(d, key_a, key_b):
    """从dict中取两个值相减"""
    a = d.get(key_a)
    b = d.get(key_b)
    if a is None or b is None:
        return None
    return float(a) - float(b)


# ===== 公开接口 =====

def calc_single(formula_name, data_row):
    """对单条数据计算派生指标

    Args:
        formula_name: 公式名，如"销售毛利率"
        data_row: dict，包含基础字段值，如 {"total_operating_revenue": 100000, ...}

    Returns:
        {"formula": "销售毛利率", "value": 45.12, "unit": "%", "fields_used": [...]}
    """
    if formula_name not in FORMULAS:
        return {"error": f"未知公式: {formula_name}", "available": list(FORMULAS.keys())}

    f = FORMULAS[formula_name]
    value = f["calc"](data_row)
    return {
        "formula": formula_name,
        "expression": f["formula"],
        "value": value,
        "unit": f["unit"],
        "fields_used": f["fields"],
    }


def calc_batch(formula_name, data_rows):
    """对多条数据批量计算派生指标

    Args:
        formula_name: 公式名
        data_rows: list of dict

    Returns:
        list of {stock_abbr, report_year, report_period, formula, value, unit}
    """
    results = []
    for row in data_rows:
        r = calc_single(formula_name, row)
        results.append({
            "stock_abbr": row.get("stock_abbr", ""),
            "report_year": row.get("report_year", ""),
            "report_period": row.get("report_period", ""),
            **r,
        })
    return results


def calc_cagr(start_value, end_value, years):
    """计算复合增长率 (CAGR)

    Args:
        start_value: 起始值
        end_value: 终止值
        years: 年数

    Returns:
        CAGR百分比，如 15.23 表示 15.23%
    """
    if start_value is None or end_value is None or years <= 0:
        return None
    try:
        start = float(start_value)
        end = float(end_value)
        if start <= 0 or end <= 0:
            return None
        cagr = (pow(end / start, 1.0 / years) - 1) * 100
        return round(cagr, 4)
    except:
        return None


def calc_yoy(current, previous):
    """计算同比增长率

    Args:
        current: 本期值
        previous: 去年同期值

    Returns:
        百分比，如 15.23 表示同比增长 15.23%
    """
    if current is None or previous is None or previous == 0:
        return None
    try:
        return round((float(current) - float(previous)) / abs(float(previous)) * 100, 4)
    except:
        return None


def calc_qoq(current, previous):
    """计算环比增长率"""
    return calc_yoy(current, previous)  # 公式相同


def list_formulas():
    """列出所有可用公式"""
    result = []
    for name, f in FORMULAS.items():
        result.append({
            "name": name,
            "formula": f["formula"],
            "unit": f["unit"],
            "fields": f["fields"],
        })
    return result


if __name__ == "__main__":
    # 测试
    print("=== 可用公式 ===")
    for f in list_formulas():
        print(f"  {f['name']}: {f['formula']} ({f['unit']})")

    print("\n=== 测试计算 ===")
    test_row = {
        "stock_abbr": "测试公司",
        "total_operating_revenue": 100000,
        "operating_expense_cost_of_sales": 55000,
        "net_profit": 15000,
        "liability_total_liabilities": 40000,
        "asset_total_assets": 100000,
    }
    for name in ["销售毛利率", "销售净利率", "资产负债率"]:
        r = calc_single(name, test_row)
        print(f"  {r['formula']} = {r['value']}{r['unit']}")

    print(f"\n  CAGR: {calc_cagr(100, 200, 3)}%")
    print(f"  同比: {calc_yoy(120, 100)}%")
