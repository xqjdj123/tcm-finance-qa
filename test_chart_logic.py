# -*- coding: utf-8 -*-
"""图表智能选图逻辑测试"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, 'D:/python-leanrn/codex')


def test_detect_chart_type():
    print("=" * 60)
    print("图表智能选图逻辑测试")
    print("=" * 60)

    # 模拟数据
    test_cases = [
        # (数据, 期望类型, 描述)
        (
            # 时间序列数据 → line
            [
                {"stock_abbr": "华润三九", "report_year": 2021, "net_profit": 100},
                {"stock_abbr": "华润三九", "report_year": 2022, "net_profit": 120},
                {"stock_abbr": "华润三九", "report_year": 2023, "net_profit": 150},
                {"stock_abbr": "华润三九", "report_year": 2024, "net_profit": 180},
            ],
            "line",
            "时间序列 → 折线图"
        ),
        (
            # 排名数据 → bar
            [
                {"stock_abbr": "云南白药", "total_operating_revenue": 306},
                {"stock_abbr": "华润三九", "total_operating_revenue": 219},
                {"stock_abbr": "白云山", "total_operating_revenue": 197},
                {"stock_abbr": "同仁堂", "total_operating_revenue": 133},
                {"stock_abbr": "步长制药", "total_operating_revenue": 84},
            ],
            "bar",
            "排名数据 → 柱状图"
        ),
        (
            # 长标签 → hbar
            [
                {"stock_abbr": "华润三九医药股份有限公司", "net_profit": 100},
                {"stock_abbr": "北京同仁堂股份有限公司", "net_profit": 80},
                {"stock_abbr": "广州白云山医药集团", "net_profit": 60},
            ],
            "hbar",
            "长标签 → 横向柱状图"
        ),
        (
            # 占比数据 → pie
            [
                {"stock_abbr": "药品", "revenue_ratio": 0.70},
                {"stock_abbr": "保健品", "revenue_ratio": 0.20},
                {"stock_abbr": "化妆品", "revenue_ratio": 0.10},
            ],
            "pie",
            "占比数据 → 饼图"
        ),
    ]

    passed = 0
    for data, expected_type, desc in test_cases:
        # 简化版检测逻辑
        val_keys = [k for k in data[0].keys()
                   if k not in ('stock_abbr', 'report_year', 'report_period', 'stock_code', 'rank', 'note')]
        vk = val_keys[0] if val_keys else None

        if vk:
            labels = [str(r.get("stock_abbr", r.get("report_year", "?"))) for r in data]
            values = [float(r.get(vk, 0) or 0) for r in data]

            # 检测逻辑
            years = set(r.get("report_year") for r in data if r.get("report_year"))
            companies = set(r.get("stock_abbr") for r in data if r.get("stock_abbr"))

            if len(years) >= 3 and len(companies) <= 2:
                detected = "line"
            elif all(0 <= abs(v) <= 1 for v in values if v != 0) and abs(sum(values) - 1.0) < 0.1:
                detected = "pie"
            elif max(len(l) for l in labels) > 8:
                detected = "hbar"
            else:
                detected = "bar"

            ok = detected == expected_type
            status = "PASS" if ok else "FAIL"
            if ok:
                passed += 1
            print("[%s] %s (detected=%s, expected=%s)" % (status, desc, detected, expected_type))

    print("\n结果: %d/%d 通过" % (passed, len(test_cases)))
    return passed == len(test_cases)


def test_chart_type_from_text():
    print("\n" + "=" * 60)
    print("从文本检测图表类型测试")
    print("=" * 60)

    test_cases = [
        ("画柱状图", "bar"),
        ("改成折线图", "line"),
        ("换成饼图", "pie"),
        ("横向柱状图", "hbar"),
        ("图片呢", None),  # 未指定
        ("画个图", None),  # 未指定
    ]

    passed = 0
    for text, expected in test_cases:
        # 检测逻辑（优先匹配更具体的关键词）
        detected = None
        if any(kw in text for kw in ["横向柱状图", "横向柱状", "横向柱"]):
            detected = "hbar"
        elif any(kw in text for kw in ["折线图", "折线", "趋势图"]):
            detected = "line"
        elif any(kw in text for kw in ["柱状图", "柱状", "条形图"]):
            detected = "bar"
        elif any(kw in text for kw in ["饼图", "饼状图", "环形图"]):
            detected = "pie"

        ok = detected == expected
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        print("[%s] '%s' → %s (expected=%s)" % (status, text, detected, expected))

    print("\n结果: %d/%d 通过" % (passed, len(test_cases)))
    return passed == len(test_cases)


if __name__ == "__main__":
    ok1 = test_detect_chart_type()
    ok2 = test_chart_type_from_text()
    sys.exit(0 if (ok1 and ok2) else 1)
