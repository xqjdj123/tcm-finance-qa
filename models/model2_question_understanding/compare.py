# -*- coding: utf-8 -*-
"""compare.py: 多公司对比、排名、交集
接收sql_query的批量结果，做对比分析。
不直接查数据库，只做数据处理。
"""


def rank(data_rows, field, top_k=None, ascending=False):
    """按指定字段排名

    Args:
        data_rows: list of dict，每条需有 stock_abbr, report_year, field
        field: 排序字段名
        top_k: 取前N名，None则返回全部
        ascending: True=升序（从小到大），False=降序（从大到小）

    Returns:
        排序后的list，每条加 rank 字段
    """
    valid = [r for r in data_rows if r.get(field) is not None]
    valid.sort(key=lambda x: float(x.get(field, 0)), reverse=not ascending)
    if top_k:
        valid = valid[:top_k]
    for i, row in enumerate(valid, 1):
        row["rank"] = i
    return valid


def intersect_rank(data_rows_1, data_rows_2, top_k=None):
    """两个排名取交集（如：营收TOP5 ∩ 净利润TOP5）

    Args:
        data_rows_1: 第一个排名结果
        data_rows_2: 第二个排名结果
        top_k: 每个排名取前N

    Returns:
        同时出现在两个排名中的公司
    """
    names_1 = set(r.get("stock_abbr", "") for r in data_rows_1[:top_k] if r.get("stock_abbr"))
    names_2 = set(r.get("stock_abbr", "") for r in data_rows_2[:top_k] if r.get("stock_abbr"))
    common = names_1 & names_2

    # 合并两个排名的数据
    result = []
    for name in common:
        row = {"stock_abbr": name}
        for r in data_rows_1:
            if r.get("stock_abbr") == name:
                row.update(r)
                break
        for r in data_rows_2:
            if r.get("stock_abbr") == name:
                # 加上第二个排名的字段（不覆盖）
                for k, v in r.items():
                    if k not in row:
                        row[k] = v
                break
        result.append(row)
    return result


def filter_rows(data_rows, conditions):
    """多条件筛选

    Args:
        data_rows: list of dict
        conditions: list of {"field": str, "op": str, "value": float}
            op: ">", "<", ">=", "<=", "==", "!="

    Returns:
        满足所有条件的行
    """
    ops = {
        ">": lambda a, b: a > b,
        "<": lambda a, b: a < b,
        ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b,
        "==": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
    }

    result = []
    for row in data_rows:
        match = True
        for cond in conditions:
            field = cond["field"]
            op = cond["op"]
            value = cond["value"]
            row_val = row.get(field)
            if row_val is None:
                match = False
                break
            try:
                if not ops[op](float(row_val), float(value)):
                    match = False
                    break
            except (ValueError, TypeError):
                match = False
                break
        if match:
            result.append(row)
    return result


def group_stats(data_rows, field):
    """计算一组数据的统计信息

    Args:
        data_rows: list of dict
        field: 要统计的字段

    Returns:
        {"count": N, "mean": X, "median": X, "min": X, "max": X, "std": X}
    """
    values = []
    for row in data_rows:
        v = row.get(field)
        if v is not None:
            try:
                values.append(float(v))
            except:
                pass

    if not values:
        return {"count": 0, "mean": None, "median": None, "min": None, "max": None, "std": None}

    values.sort()
    n = len(values)
    mean = sum(values) / n
    median = values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2
    variance = sum((x - mean) ** 2 for x in values) / n
    std = variance ** 0.5

    return {
        "count": n,
        "mean": round(mean, 4),
        "median": round(median, 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "std": round(std, 4),
    }


def compare_companies(data_rows, companies, fields):
    """多公司指定字段对比

    Args:
        data_rows: 全部数据
        companies: 要对比的公司名列表
        fields: 要对比的字段列表

    Returns:
        list of dict，每个公司一行，包含指定字段
    """
    result = []
    for company in companies:
        matches = [r for r in data_rows if r.get("stock_abbr") == company]
        if not matches:
            continue
        # 取最新的一条
        row = max(matches, key=lambda r: (r.get("report_year", 0), r.get("report_period", "")))
        out = {"stock_abbr": company, "report_year": row.get("report_year"), "report_period": row.get("report_period")}
        for f in fields:
            out[f] = row.get(f)
        result.append(out)
    return result


def build_comparison_table(data_rows, label_field="stock_abbr", value_fields=None):
    """构建对比表（用于前端展示）

    Args:
        data_rows: list of dict
        label_field: 行标签字段（通常是stock_abbr）
        value_fields: 值字段列表

    Returns:
        {"headers": [...], "rows": [[...], ...]}
    """
    if not value_fields:
        value_fields = [k for k in data_rows[0].keys() if k not in (label_field, "report_year", "report_period", "stock_code", "rank")]

    headers = [label_field, "年份"] + value_fields
    rows = []
    for r in data_rows:
        row = [r.get(label_field, ""), str(r.get("report_year", ""))]
        for f in value_fields:
            row.append(r.get(f, ""))
        rows.append(row)
    return {"headers": headers, "rows": rows}


if __name__ == "__main__":
    # 测试
    test_data = [
        {"stock_abbr": "白云山", "report_year": 2025, "net_profit": 400000, "total_operating_revenue": 7000000},
        {"stock_abbr": "云南白药", "report_year": 2025, "net_profit": 300000, "total_operating_revenue": 4000000},
        {"stock_abbr": "片仔癀", "report_year": 2025, "net_profit": 250000, "total_operating_revenue": 1000000},
        {"stock_abbr": "同仁堂", "report_year": 2025, "net_profit": 200000, "total_operating_revenue": 2000000},
        {"stock_abbr": "华润三九", "report_year": 2025, "net_profit": 150000, "total_operating_revenue": 3000000},
    ]

    print("=== 排名 TOP3 ===")
    for r in rank(test_data, "net_profit", top_k=3):
        print(f"  #{r['rank']} {r['stock_abbr']}: {r['net_profit']}")

    print("\n=== 统计 ===")
    stats = group_stats(test_data, "net_profit")
    print(f"  均值={stats['mean']} 中位数={stats['median']} 标准差={stats['std']}")

    print("\n=== 筛选 净利润>200000 ===")
    for r in filter_rows(test_data, [{"field": "net_profit", "op": ">", "value": 200000}]):
        print(f"  {r['stock_abbr']}: {r['net_profit']}")
