# -*- coding: utf-8 -*-
"""T3 (B3) 追问上下文断裂 — 回归单测。

背景: app.py 以前在 add_turn 里存占位 slots {"from": "react"}，且从不调用
      session.update_understanding()，导致第二轮"那营收呢"无法继承上一轮的
      公司/年份/指标（get_context_for_llm 读不到 slots）。

修法（纯函数 + 接线，双保险可单测）:
  1) agent/session.py 新增 extract_understanding_from_result(result)：从
     result/tool_calls_log 的 sql_query arguments（understanding 嵌套 /
     扁平键）及 result_parsed 规范化结果中解析 company/indicator/year，
     无可用信息安全返回空列表；
  2) app.py 在 agent.run() 后回填 session.update_understanding()，并把
     add_turn 的占位 slots 换成真实 session.slots。

本测试不跑真实 LLM / DB：
  - 纯函数部分用构造的 tool_calls_log 直接喂给解析器；
  - app 接线部分用 stub Agent（返回构造 AgentResult）+ 内存 SessionManager，
    验证两轮连续提问后第二轮的 last_context / slots 能继承"白云山 2024 净利润"。

运行(目标解释器):
    python -m pytest tests/test_agent_phase1_b3.py -q
兼容:
    python -m unittest tests.test_agent_phase1_b3 -v
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.session import Session, extract_understanding_from_result


def _sql_tc(arguments, parsed=None):
    """构造一条 sql_query tool_calls_log 条目。"""
    return {
        "tool": "sql_query",
        "arguments": arguments,
        "result_preview": '{"success": true}',
        "result_parsed": parsed if parsed is not None else {"success": True},
    }


class TestExtractPureFunction(unittest.TestCase):
    """T3-1: extract_understanding_from_result 纯函数解析各种 sql_query 参数形态。"""

    def test_extract_from_nested_understanding(self):
        """LLM 传结构化 understanding（嵌套）时应解析出三个槽位。"""
        log = [_sql_tc({
            "question": "白云山2024年净利润",
            "understanding": {
                "companies": ["白云山"],
                "indicators": ["净利润"],
                "years": [2024],
            },
            "query_type": "auto",
        })]
        result = extract_understanding_from_result({"tool_calls_log": log})
        self.assertEqual(result["companies"], ["白云山"])
        self.assertEqual(result["indicators"], ["净利润"])
        self.assertEqual(result["years"], ["2024"])  # int 2024 归一为字符串

    def test_extract_from_flat_arguments(self):
        """LLM 只传扁平键（company/indicator/year）时同样可解析。"""
        log = [_sql_tc({
            "question": "云南白药2023年营业收入",
            "company": "云南白药",
            "indicator": "营业收入",
            "year": "2023年",
        })]
        result = extract_understanding_from_result(log)  # 直接传原始 list 也可
        self.assertEqual(result["companies"], ["云南白药"])
        self.assertEqual(result["indicators"], ["营业收入"])
        self.assertEqual(result["years"], ["2023"])  # "2023年" 去后缀

    def test_extract_fallback_result_parsed_and_data_rows(self):
        """LLM 只传裸 question 时，从 result_parsed 规范化结果兜底解析。

        公司与年份只认请求侧（顶层 companies/company/years/year）；
        结果行 report_year 不再兜底（避免跨年结果污染请求侧年份）。
        """
        parsed = {
            "success": True,
            "company": "白云山",
            "metric": "net_profit",
            "display_metric": "净利润",
            "data": [{"stock_abbr": "白云山", "report_year": 2024,
                      "net_profit": 452000.0}],
        }
        log = [_sql_tc({"question": "白云山2024年净利润"}, parsed=parsed)]
        result = extract_understanding_from_result(log)
        self.assertEqual(result["companies"], ["白云山"])  # 顶层 company（请求侧）
        # display_metric（中文）优先于 metric（EN），且不混拼两种口径
        self.assertEqual(result["indicators"], ["净利润"])
        self.assertEqual(result["years"], [])  # 无顶层 years，data 行 report_year 不再兜底

    def test_extract_real_sql_tool_auto_output_shape(self):
        """真实链路形态：裸 question + sql_tool 自动补全后的返回（顶层回写
        understanding，数据行指标列为英文列名），指标须解析为中文字符串。"""
        parsed = {
            "success": True,
            "data": [
                {"stock_abbr": "白云山", "report_year": 2024,
                 "report_period": "FY", "net_profit": 452000.0},
            ],
            "sql": "SELECT ... WHERE stock_abbr LIKE '%白云山%' AND report_year = 2024",
            "answer": "白云山2024年净利润为45.2亿元",
            "confidence": 0.85,
            "retry_count": 0,
            "reason": "第1次尝试成功（完整参数）",
            "unit": "万元",
            "companies": ["白云山"],
            "indicators": ["净利润"],   # sql_tool 回写的自动补全结果（中文）
            "years": [2024],
        }
        log = [_sql_tc({"question": "白云山2024年净利润"}, parsed=parsed)]
        result = extract_understanding_from_result(log)
        self.assertEqual(result["companies"], ["白云山"])
        self.assertEqual(result["indicators"], ["净利润"])
        self.assertEqual(result["years"], ["2024"])

    def test_extract_indicator_from_data_row_legacy_shape(self):
        """兜底路径：返回未回写 understanding 顶层键时，从 data 行英文
        指标列解析并映射中文（taxonomy），指标仍须为非空中文。
        注意：公司/年份不再从 data 行 stock_abbr/report_year 兜底。"""
        parsed = {
            "success": True,
            "data": [
                {"stock_abbr": "白云山", "report_year": 2024,
                 "report_period": "FY", "net_profit": 452000.0},
            ],
            "sql": "SELECT ...",
            "answer": "白云山2024年净利润为45.2亿元",
            "confidence": 0.85,
            "retry_count": 0,
            "reason": "第1次尝试成功（完整参数）",
            "unit": "万元",
        }
        log = [_sql_tc({"question": "白云山2024年净利润"}, parsed=parsed)]
        result = extract_understanding_from_result(log)
        self.assertEqual(result["companies"], [])  # 无顶层 companies，不取 data 行 stock_abbr
        self.assertEqual(result["indicators"], ["净利润"])  # 指标仍可从 data 行英文列反推
        self.assertEqual(result["years"], [])  # 无顶层 years，不取 data 行 report_year

    def test_extract_safe_on_empty_and_garbage(self):
        """None / 空 dict / 无 tool_calls_log / 非 sql 工具 / 畸形数据都不抛异常。"""
        empty = {"companies": [], "indicators": [], "years": []}
        self.assertEqual(extract_understanding_from_result(None), empty)
        self.assertEqual(extract_understanding_from_result({}), empty)
        self.assertEqual(extract_understanding_from_result(object()), empty)
        self.assertEqual(extract_understanding_from_result(["not-a-dict", 42]), empty)

        # 非 sql_query 工具（如 chart）不参与槽位解析
        chart_log = [{
            "tool": "chart",
            "arguments": {"data": [{"stock_abbr": "白云山", "report_year": 2024}]},
        }]
        self.assertEqual(extract_understanding_from_result(chart_log), empty)

        # 畸形嵌套：arguments 不是 dict / understanding 是字符串
        bad = [_sql_tc("裸字符串"), _sql_tc({"question": "x", "understanding": "boom"})]
        self.assertEqual(extract_understanding_from_result(bad), empty)

        # result_parsed.data 含 None / 非 dict 行不崩溃；且不再从 data 行取公司
        parsed = {"success": True, "data": [None, "bad", {"stock_abbr": "白云山"}]}
        result = extract_understanding_from_result(
            [_sql_tc({"question": "x"}, parsed=parsed)])
        self.assertEqual(result["companies"], [])

    def test_extract_dedup_multi_calls(self):
        """多条 sql_query（如趋势多指标）跨调用合并去重，保持顺序。"""
        log = [
            _sql_tc({"understanding": {"companies": ["白云山"],
                                       "indicators": ["净利润"], "years": [2024]}}),
            _sql_tc({"understanding": {"companies": ["白云山"],
                                       "indicators": ["营业收入"], "years": [2023]}}),
        ]
        result = extract_understanding_from_result(log)
        self.assertEqual(result["companies"], ["白云山"])
        self.assertEqual(result["indicators"], ["净利润", "营业收入"])
        self.assertEqual(result["years"], ["2024", "2023"])

    def test_extract_never_takes_year_or_company_from_data_rows(self):
        """槽位来源纯度：公司与年份只认请求侧，data 行 report_year /
        stock_abbr 不得回填（跨年结果不得污染请求侧年份，P0 回归）。"""
        parsed = {
            "success": True,
            "data": [
                {"stock_abbr": "白云山", "report_year": 2022, "net_profit": 1.0},
                {"stock_abbr": "白云山", "report_year": 2023, "net_profit": 2.0},
                {"stock_abbr": "白云山", "report_year": 2024, "net_profit": 3.0},
            ],
        }
        log = [_sql_tc({"question": "白云山2024年净利润"}, parsed=parsed)]
        result = extract_understanding_from_result(log)
        self.assertEqual(result["companies"], [])
        self.assertEqual(result["years"], [])
        # 指标仍可从 data 行英文列反推（"查了哪个字段"是事实）
        self.assertEqual(result["indicators"], ["净利润"])

    def test_empty_understanding_never_replaces_prior_slots(self):
        """空解析结果 update_understanding 后不得清空已继承的槽位。"""
        session = Session("t3_noop")
        session.update_understanding({"companies": ["白云山"],
                                      "indicators": ["净利润"], "years": ["2024"]})
        empty = extract_understanding_from_result(None)
        session.update_understanding(empty)
        ctx = session.get_context_for_llm()
        self.assertEqual(ctx["company"], "白云山")
        self.assertEqual(ctx["year"], "2024")
        self.assertEqual(ctx["indicator"], "净利润")


class TestRealSQLToolAutoOutput(unittest.TestCase):
    """sql_tool 真实 auto 路径（mock DB/模型）：返回 JSON 顶层回写自动补全的
    understanding，中文指标标签可被 session 解析器取到。"""

    def test_sql_tool_run_writes_back_understanding_and_session_parses(self):
        import types
        from agent.tools.sql_tool import SQLTool

        tool = SQLTool()
        understanding = {
            "intent": "basic_query",
            "companies": ["白云山"],
            "indicators": ["net_profit"],
            "years": [2024],
            "period": "FY",
            "top_k": None,
            "entities": {"COMP": ["白云山"], "METRIC": ["net_profit"],
                         "PERIOD": ["FY"]},
        }

        canned_rows = [
            {"stock_abbr": "白云山", "report_year": 2024, "report_period": "FY",
             "net_profit": 452000.0},
        ]

        # 禁止真实 DB / pipeline / LLM：全部打桩到确定性返回值
        with mock.patch.object(SQLTool, "_get_conn",
                               side_effect=AssertionError("不应连接真实DB")), \
             mock.patch.object(SQLTool, "_get_pipeline",
                               side_effect=AssertionError("不应加载真实pipeline")), \
             mock.patch.object(SQLTool, "_column_exists", return_value=True), \
             mock.patch.object(SQLTool, "_execute_sql", return_value=canned_rows), \
             mock.patch.object(SQLTool, "_format_answer",
                               return_value="白云山2024年净利润为45.2亿元"), \
             mock.patch.dict(sys.modules, {
                 "sql_generator": types.ModuleType("sql_generator"),
             }):
            fake_gen_cls = mock.Mock()
            fake_gen_cls.return_value.generate.return_value = \
                "SELECT net_profit FROM income_sheet LIMIT 1"
            sys.modules["sql_generator"].SQLGenerator = fake_gen_cls
            out = tool.run({
                "question": "白云山2024年净利润",
                "understanding": dict(understanding),
                "query_type": "auto",
            })

        self.assertTrue(out["success"])
        # 顶层回写自动补全后的 understanding（只增键）
        self.assertEqual(out["companies"], ["白云山"])
        self.assertEqual(out["years"], [2024])
        self.assertEqual(out["indicators"], ["净利润"])  # 英文列名 → 中文标签
        self.assertEqual(out["data"], canned_rows)
        # 解析器可从 sql_tool 真实返回体取到中文指标
        parsed_u = extract_understanding_from_result(
            {"tool_calls_log": [_sql_tc({"question": "白云山2024年净利润"},
                                        parsed=out)]})
        self.assertEqual(parsed_u["companies"], ["白云山"])
        self.assertEqual(parsed_u["indicators"], ["净利润"])
        self.assertEqual(parsed_u["years"], ["2024"])


class TestSessionFollowupInheritance(unittest.TestCase):
    """T3-2: 解析结果回填 Session 后，第二轮追问能继承上轮槽位。"""

    def test_two_turn_inherit_company_and_year(self):
        session = Session("t3_unit")

        turn1 = _sql_tc({
            "understanding": {"companies": ["白云山"],
                              "indicators": ["净利润"], "years": [2024]},
        })
        u1 = extract_understanding_from_result([turn1])
        self.assertTrue(any(u1.values()))
        session.update_understanding(u1)

        ctx1 = session.get_context_for_llm()
        self.assertEqual(ctx1["company"], "白云山")
        self.assertEqual(ctx1["indicator"], "净利润")
        self.assertEqual(ctx1["year"], "2024")

        # 第二轮"那营收呢"——最坏情况 LLM 只给新指标，不重复公司/年份
        turn2 = _sql_tc({"understanding": {"indicators": ["营业收入"]}})
        u2 = extract_understanding_from_result([turn2])
        session.update_understanding(u2)

        ctx2 = session.get_context_for_llm()
        self.assertEqual(ctx2["company"], "白云山", "公司槽位被清空，追问断裂")
        self.assertEqual(ctx2["year"], "2024", "年份槽位被清空，追问断裂")
        self.assertEqual(ctx2["indicator"], "营业收入", "新指标未覆盖")


class TestAppWiring(unittest.TestCase):
    """T3-3: app.process_question 在 agent.run() 后回填 session + 存真实 slots。"""

    def test_two_turns_with_real_sql_tool_output_shape(self):
        """真实链路两轮：裸 question + sql_tool 返回体（data 行英文指标列 /
        顶层回写中文 understanding）——指标槽位取得到、第二轮"那营收呢"
        能继承首轮净利润并更新为营业收入。"""
        import app as app_module
        from agent.engine import AgentResult

        # sql_tool 真实输出：data 行指标列 = 本次实际执行的英文列名；
        # 自动补全后的 understanding 在 sql_tool 里以顶层键回写。
        turn1_parsed = {
            "success": True,
            "data": [{"stock_abbr": "白云山", "report_year": 2024,
                      "report_period": "FY", "net_profit": 452000.0}],
            "sql": "SELECT ... LIMIT 1",
            "answer": "白云山2024年净利润为45.2亿元",
            "confidence": 0.85, "retry_count": 0,
            "reason": "第1次尝试成功（完整参数）", "unit": "万元",
            "companies": ["白云山"], "indicators": ["净利润"], "years": [2024],
        }
        turn2_parsed = {
            "success": True,
            "data": [{"stock_abbr": "白云山", "report_year": 2024,
                      "report_period": "FY",
                      "total_operating_revenue": 7552000.0}],
            "sql": "SELECT ... LIMIT 1",
            "answer": "白云山2024年营业收入为755.2亿元",
            "confidence": 0.85, "retry_count": 0,
            "reason": "第1次尝试成功（完整参数）", "unit": "万元",
            "companies": ["白云山"], "indicators": ["营业收入"],
            "years": [2024],
        }

        class _FakeAgent:
            def __init__(self):
                self.calls = []

            def run(self, question, history=None, session_id="default",
                    last_context=None):
                self.calls.append(last_context)
                parsed = turn2_parsed if question.startswith("那营收") else turn1_parsed
                return AgentResult(
                    answer=parsed["answer"],
                    source="agent",
                    sql_data=parsed["data"],
                    chart_html=None,
                    trace={"spans": []},
                    tool_calls_log=[_sql_tc({"question": question}, parsed=parsed)],
                )

        fake_agent = _FakeAgent()
        mgr = mock.Mock()
        session = Session("t3_web_real")
        mgr.get_or_create.return_value = session

        with mock.patch("app.get_agent", return_value=fake_agent), \
             mock.patch("agent.session.get_session_manager", return_value=mgr):
            r1 = app_module.process_question("白云山2024年净利润", session_id="t3_web_real")
            r2 = app_module.process_question("那营收呢", session_id="t3_web_real")

        self.assertNotIn("处理失败", r1["answer"])
        self.assertNotIn("处理失败", r2["answer"])
        # 第一轮：company/year/indicator 全部回填（真实链路不再指标恒空）
        ctx2 = fake_agent.calls[1]
        self.assertEqual(ctx2["company"], "白云山")
        self.assertEqual(ctx2["year"], "2024")
        self.assertEqual(ctx2["indicator"], "净利润")
        # 第二轮：指标更新为营业收入，供第三轮继承
        self.assertEqual(session.slots["companies"], ["白云山"])
        self.assertEqual(session.slots["years"], ["2024"])
        self.assertEqual(session.slots["indicators"], ["营业收入"])

    def test_process_question_backfills_session_and_keeps_return_shape(self):
        import app as app_module
        from agent.engine import AgentResult

        class _FakeAgent:
            def __init__(self):
                self.calls = []

            def run(self, question, history=None, session_id="default",
                    last_context=None):
                self.calls.append(last_context)
                if question.startswith("那营收"):
                    return AgentResult(
                        answer="白云山2024年营业收入为755.2亿元",
                        source="agent",
                        sql_data=[{"stock_abbr": "白云山", "report_year": 2024,
                                   "total_operating_revenue": 7552000.0}],
                        chart_html=None,
                        trace={"spans": []},
                        tool_calls_log=[_sql_tc({
                            "question": question,
                            "understanding": {"indicators": ["营业收入"]},
                        })],
                    )
                return AgentResult(
                    answer="白云山2024年净利润为45.2亿元",
                    source="agent",
                    sql_data=[{"stock_abbr": "白云山", "report_year": 2024,
                               "net_profit": 452000.0}],
                    chart_html=None,
                    trace={"spans": []},
                    tool_calls_log=[_sql_tc({
                        "question": question,
                        "understanding": {
                            "companies": ["白云山"],
                            "indicators": ["净利润"],
                            "years": [2024],
                        },
                    })],
                )

        fake_agent = _FakeAgent()
        mgr = mock.Mock()
        session = Session("t3_web")
        mgr.get_or_create.return_value = session

        with mock.patch("app.get_agent", return_value=fake_agent), \
             mock.patch("agent.session.get_session_manager", return_value=mgr):
            r1 = app_module.process_question("白云山2024年净利润", session_id="t3_web")
            r2 = app_module.process_question("那营收呢", session_id="t3_web")

        # 1) 返回结构字段照旧（不破坏现有契约）
        for key in ("question", "task_type", "intent", "plan", "confidence",
                    "answer", "display_html", "rag_html_raw", "chart",
                    "result_count", "trace"):
            self.assertIn(key, r1, f"process_question 返回缺少 {key}")
            self.assertIn(key, r2, f"process_question 返回缺少 {key}")
        self.assertNotIn("处理失败", r1["answer"])
        self.assertNotIn("处理失败", r2["answer"])

        # 2) 第一轮 add_turn 存的是真实槽位（替代 {"from":"react"} 占位）
        first_turn = session.history[0]["slots"]
        self.assertNotIn("from", first_turn, "占位 slots 未替换")
        self.assertEqual(first_turn["companies"], ["白云山"])
        self.assertEqual(first_turn["years"], ["2024"])

        # 3) 第二轮 agent.run() 收到的 last_context 含上轮公司/年份/指标
        self.assertEqual(len(fake_agent.calls), 2)
        ctx_for_turn2 = fake_agent.calls[1]
        self.assertEqual(ctx_for_turn2["company"], "白云山",
                         "第二轮上下文丢失公司")
        self.assertEqual(ctx_for_turn2["year"], "2024", "第二轮上下文丢失年份")
        self.assertEqual(ctx_for_turn2["indicator"], "净利润",
                         "第二轮上下文丢失指标")

        # 4) 第二轮结束后：公司/年份槽位保留、新指标已覆盖（供第三轮继续继承）
        self.assertEqual(session.slots["companies"], ["白云山"])
        self.assertEqual(session.slots["years"], ["2024"])
        self.assertEqual(session.slots["indicators"], ["营业收入"])
        last_turn = session.get_last_turn()
        self.assertEqual(last_turn["slots"]["companies"], ["白云山"])
        self.assertEqual(last_turn["slots"]["indicators"], ["营业收入"])
        ctx2 = session.get_context_for_llm()
        self.assertEqual(ctx2["company"], "白云山")
        self.assertEqual(ctx2["year"], "2024")
        self.assertEqual(ctx2["indicator"], "营业收入")


if __name__ == "__main__":
    unittest.main(verbosity=2)
