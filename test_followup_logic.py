# -*- coding: utf-8 -*-
"""追问逻辑测试：不依赖数据库，只测试追问检测逻辑"""
import sys, json
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, 'D:/python-leanrn/codex')
from agent.session import Session, SessionManager


def test_followup_detection():
    print("=" * 60)
    print("追问检测逻辑测试")
    print("=" * 60)

    # 模拟一个有历史数据的 session
    session = Session("test")
    session.add_turn(
        question="2025年Q3营收排名前五的中药企业",
        answer="排名结果...",
        slots={"company": None, "indicator": "total_operating_revenue", "year": 2025, "period": "Q3"},
        data=[
            {"stock_abbr": "华润三九", "total_operating_revenue": 50000, "report_year": 2025, "report_period": "Q3"},
            {"stock_abbr": "云南白药", "total_operating_revenue": 45000, "report_year": 2025, "report_period": "Q3"},
            {"stock_abbr": "同仁堂", "total_operating_revenue": 40000, "report_year": 2025, "report_period": "Q3"},
            {"stock_abbr": "片仔癀", "total_operating_revenue": 35000, "report_year": 2025, "report_period": "Q3"},
            {"stock_abbr": "白云山", "total_operating_revenue": 30000, "report_year": 2025, "report_period": "Q3"},
        ],
        task_type="query"
    )

    # 测试追问检测
    chart_kws = ["图片", "画图", "图表", "柱状图", "折线图", "可视化", "画一下", "画个图"]
    rank_kws = ["第二名", "第三名", "第四名", "第五名", "前三", "前五", "前十",
                "第一", "第二", "第三", "第四", "第五", "最后一名", "排名"]

    test_cases = [
        ("图片呢", chart_kws, "图表追问"),
        ("画个图", chart_kws, "图表追问"),
        ("柱状图", chart_kws, "图表追问"),
        ("第二名是谁", rank_kws, "排名追问"),
        ("前三是哪些", rank_kws, "排名追问"),
        ("片仔癀净利润是多少", [], "普通问题（非追问）"),
    ]

    passed = 0
    for question, keywords, expected_type in test_cases:
        is_followup = any(kw in question for kw in keywords)
        last = session.history[-1]
        has_data = bool(last.get("data"))

        if keywords:  # 应该是追问
            ok = is_followup and has_data
            status = "PASS" if ok else "FAIL"
            if ok:
                passed += 1
        else:  # 不应该是追问
            ok = not is_followup
            status = "PASS" if ok else "FAIL"
            if ok:
                passed += 1

        print("[%s] %s → %s (is_followup=%s, has_data=%s)" % (
            status, question, expected_type, is_followup, has_data
        ))

    # 测试排名提取
    print("\n" + "=" * 60)
    print("排名数字提取测试")
    print("=" * 60)

    import re
    CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

    def extract_rank(q):
        m = re.search(r"第([一二三四五六七八九十\d]+)", q)
        if m:
            token = m.group(1)
            return int(token) if token.isdigit() else CN_NUM.get(token)
        m = re.search(r"前([一二三四五六七八九十\d]+)", q)
        if m:
            token = m.group(1)
            return int(token) if token.isdigit() else CN_NUM.get(token)
        return None

    rank_cases = [
        ("第二名是谁", 2),
        ("第三名呢", 3),
        ("前五名", 5),
        ("前三是哪些", 3),
        ("排名", None),  # 没有具体数字
    ]

    for question, expected in rank_cases:
        result = extract_rank(question)
        ok = result == expected
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        print("[%s] %s → %s (expected=%s)" % (status, question, result, expected))

    total = len(test_cases) + len(rank_cases)
    print("\n" + "=" * 60)
    print("结果: %d/%d 通过" % (passed, total))
    return passed == total


if __name__ == "__main__":
    success = test_followup_detection()
    sys.exit(0 if success else 1)
