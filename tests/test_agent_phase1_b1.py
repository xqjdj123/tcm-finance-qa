# -*- coding: utf-8 -*-
"""T1 (B1) report/risk 字段契约修复 — 回归单测。

背景: app.py:163 读 tc["result_parsed"]，engine.py 此前只存 result_preview，
      字段名不一致 -> report / risk_alerts 在 process_question 中恒为 None。
修法: engine.py 写 tool_calls_log 时同时保存 result_parsed = 解析后的 dict。

本测试不碰真实 LLM / DB：用 stub LLM 驱动 engine._run_async 走完
"工具调用 -> 结果注入 -> 最终回答" 全流程，再按 app.py 的读取方式
(tc.get("result_parsed")) 断言能取到 report / alerts。

运行(目标解释器):
    python -m pytest tests/test_agent_phase1_b1.py -q
兼容:
    python -m unittest tests.test_agent_phase1_b1 -v
"""
import asyncio
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.engine import ReActAgent, AgentResult
from agent.llm import LLMResponse, ToolCall

REPORT_JSON = {
    "success": True,
    "company": "白云山",
    "report": "# 白云山财务分析报告\n\n**公司概况** ...",
    "metrics": {"净利润": 452000.0},
    "alerts": [
        {"type": "盈利风险", "rule": "净利润下降", "severity": "高",
         "detail": "白云山 2024年净利润=40.0亿 2023年净利润=45.0亿"},
    ],
    "data": [{"stock_abbr": "白云山", "report_year": 2024}],
}
RISK_JSON = {
    "success": True,
    "company": "白云山",
    "alerts": [
        {"type": "盈利风险", "rule": "净利润下降", "severity": "高",
         "detail": "白云山净利润连续2年下降", "suggestion": "盈利能力下滑"},
    ],
}


class _FakeLLM:
    """按序返回: 1) report 工具调用 2) risk_analysis 工具调用 3) 最终文本。"""

    def __init__(self):
        self.calls = 0
        self.responses = [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(id="call_report", name="report",
                             arguments={"company": "白云山"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(id="call_risk", name="risk_analysis",
                             arguments={"company": "白云山"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="已生成白云山财务分析报告及风险预警。"),
        ]

    def chat_with_tools(self, messages, tools, tool_choice="auto"):
        resp = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return resp


class _FakeRegistry:
    """按工具名返回与真实 report/risk 工具同构的 JSON 字符串。"""

    async def execute(self, name, **kwargs):
        body = {"report": REPORT_JSON, "risk_analysis": RISK_JSON}.get(name)
        if body is None:
            body = {"success": False, "error": "unknown tool"}
        return json.dumps(body, ensure_ascii=False)


    def to_openai_schemas(self):
        return []

    def get_tool_descriptions(self):
        return []


class _FakePromptManager:
    """最小 prompt_manager：不触发真实系统提示词。"""

    def build_system_prompt(self, **kwargs):
        return "[system]"

    def build_context_hint(self, **kwargs):
        return ""



class _FakeTracer:
    """最小 tracer：仅要求 engine 用到的方法可空跑。"""

    class _Ctx:
        def __enter__(self):
            return None

        def __exit__(self, *exc):
            return False

    def trace(self, *a, **k):
        return self._Ctx()

    def span(self, *a, **k):
        return self._Ctx()

    def add_event(self, *a, **k):
        pass

    def to_dict(self):
        return {"spans": []}


def _build_result() -> AgentResult:
    """构造 ReActAgent，跑完 report + risk_analysis 两条工具链路。"""
    agent = object.__new__(ReActAgent)
    agent.max_iterations = 8
    agent.llm = _FakeLLM()
    agent.tool_registry = _FakeRegistry()
    agent.prompt_manager = _FakePromptManager()
    agent.tracer = _FakeTracer()

    return asyncio.run(
        agent._run_async(
            "生成白云山财务分析报告",
            history=[],
            session_id="t1_test",
            last_context=None,
        )
    )


class TestReportRiskContract(unittest.TestCase):
    """T1: engine 写 result_parsed，app.py 读取契约成立。"""

    def test_tool_log_has_result_parsed_contract(self):
        """每个工具调用都必须带 result_parsed（与 result_preview 并存）。"""
        result = _build_result()
        self.assertEqual(len(result.tool_calls_log), 2)
        for tc in result.tool_calls_log:
            self.assertIn("result_parsed", tc,
                          f"tool_calls_log 缺少 result_parsed: {tc}")
            self.assertIsInstance(tc["result_parsed"], dict)
            self.assertIn("result_preview", tc,
                          "不得删除既有字段 result_preview")

    def test_app_reading_logic_gets_report(self):
        """复刻 app.py 的读取逻辑：report 必须能取到，非 None。"""
        result = _build_result()
        report_md = None
        for tc in result.tool_calls_log:
            parsed = tc.get("result_parsed", {})
            if isinstance(parsed, dict) and tc.get("tool") == "report":
                report_md = parsed.get("report")
        self.assertIsNotNone(report_md, "app 侧 report 取不到")
        self.assertIn("白云山", report_md)
        self.assertEqual(result.tool_calls_log[0]["tool"], "report")
        self.assertEqual(result.tool_calls_log[0]["result_parsed"]["success"], True)

    def test_app_reading_logic_gets_risk_alerts(self):
        """复刻 app.py 的读取逻辑：risk_alerts 必须能取到，非 None。"""
        result = _build_result()
        risk_alerts = None
        for tc in result.tool_calls_log:
            parsed = tc.get("result_parsed", {})
            if isinstance(parsed, dict) and tc.get("tool") == "risk_analysis":
                risk_alerts = parsed.get("alerts")
        self.assertIsNotNone(risk_alerts, "app 侧 risk_alerts 取不到")
        self.assertEqual(len(risk_alerts), 1)
        self.assertEqual(risk_alerts[0]["severity"], "高")

    def test_loop_drives_final_answer(self):
        """全流程走通：工具调用后能回到最终回答，source=agent。"""
        result = _build_result()
        self.assertEqual(result.source, "agent")
        self.assertEqual(result.iterations, 3)
        self.assertTrue("报告" in result.answer)


if __name__ == "__main__":
    unittest.main(verbosity=2)
