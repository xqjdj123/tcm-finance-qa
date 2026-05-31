# -*- coding: utf-8 -*-
"""追问测试：验证追问检测前置修复"""
import sys, json
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, 'D:/python-leanrn/codex')
from agent.agent import FinanceAgent


def test_followup():
    agent = FinanceAgent()
    session_id = "followup_test"

    print("=" * 60)
    print("追问测试")
    print("=" * 60)

    # 第一轮：正常查询
    print("\n[Round 1] 2025年Q3营收排名前五的中药企业")
    r1 = agent.query("2025年Q3营收排名前五的中药企业", session_id=session_id)
    print("  task_type: %s" % r1.get("task_type"))
    print("  has_data: %s" % bool(r1.get("history")))
    if r1.get("history"):
        for h in r1["history"]:
            result = h.get("result", {})
            if result.get("data"):
                print("  data_rows: %d" % len(result["data"]))
                for i, row in enumerate(result["data"][:5], 1):
                    print("    %d. %s" % (i, row.get("stock_abbr", "?")))

    # 第二轮：追问图表
    print("\n[Round 2] 图片呢")
    r2 = agent.query("图片呢", session_id=session_id)
    print("  task_type: %s" % r2.get("task_type"))
    print("  has_chart: %s" % bool(r2.get("chart")))
    print("  answer: %s" % r2.get("answer", "")[:50])

    # 第三轮：追问排名
    print("\n[Round 3] 第二名是谁")
    r3 = agent.query("第二名是谁", session_id=session_id)
    print("  task_type: %s" % r3.get("task_type"))
    print("  answer: %s" % r3.get("answer", "")[:100])

    # 验证
    print("\n" + "=" * 60)
    print("验证结果")
    print("=" * 60)

    checks = [
        ("Round 2 图表追问", r2.get("task_type") == "chart" and bool(r2.get("chart"))),
        ("Round 3 排名追问", "第2名" in r3.get("answer", "") or "第二" in r3.get("answer", "")),
    ]

    passed = 0
    for desc, ok in checks:
        status = "PASS" if ok else "FAIL"
        print("[%s] %s" % (status, desc))
        if ok:
            passed += 1

    print("\n结果: %d/%d 通过" % (passed, len(checks)))
    return passed == len(checks)


if __name__ == "__main__":
    success = test_followup()
    sys.exit(0 if success else 1)
