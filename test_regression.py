# -*- coding: utf-8 -*-
"""回归测试集：每次改代码后跑一遍，确保老功能不坏"""
import sys, json
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, 'D:/python-leanrn/codex')
from agent.agent import FinanceAgent

TESTS = [
    # (问题, 期望task_type, 期望data非空, 描述)
    ("金花2023年净利润", "query", True, "基础查询"),
    ("片仔癀有什么财务风险", "risk", True, "风险预警"),
    ("生成片仔癀财务分析报告", "report", True, "财务报告"),
    ("同仁堂近三年利润趋势", "trend", True, "趋势分析"),
    ("2025年Q3营收排名前五的中药企业", "query", True, "排名查询(无公司名)"),
    ("白云山和云南白药2024年营收对比", "compare", True, "多公司对比"),
]

def run_tests():
    agent = FinanceAgent()
    passed = 0
    failed = 0

    print("=" * 60)
    print("回归测试")
    print("=" * 60)

    for question, expected_task, expect_data, desc in TESTS:
        try:
            r = agent.query(question, session_id="regression_test")
            task = r.get("task_type", "")
            has_data = bool(r.get("history")) and any(
                h.get("result", {}).get("success") for h in r.get("history", [])
            )

            task_ok = task == expected_task
            data_ok = has_data == expect_data

            if task_ok and data_ok:
                status = "PASS"
                passed += 1
            else:
                status = "FAIL"
                failed += 1

            print("[%s] %s" % (status, desc))
            if status == "FAIL":
                print("  期望: task=%s data=%s" % (expected_task, expect_data))
                print("  实际: task=%s data=%s" % (task, has_data))

        except Exception as e:
            print("[ERROR] %s: %s" % (desc, e))
            failed += 1

    print()
    print("结果: %d/%d 通过" % (passed, passed + failed))
    return failed == 0

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
