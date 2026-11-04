# -*- coding: utf-8 -*-
"""T2 (B2) chart 图表恒为空 — 回归单测。

背景: chart_tool.execute() 此前只返回 has_chart 元数据，engine.py:173-174
      用 result_data.get("chart_html") 读取 -> AgentResult.chart_html 恒为 None，
      app.py 组装响应的 "chart" 字段永远为空。
修法: chart_tool.execute() 直接序列化 run() 的结果（与 run() 对齐），
      JSON 恢复携带 chart_html；engine.py 读取逻辑保持不动。
防回退: engine 注入下一轮 LLM 的 tool 消息只放截断预览（chart_html 剔除），
      断言 LLM tool 消息不含 base64 chart_html，防上下文膨胀复发。

本测试不跑真实图表渲染 / LLM / DB：
  - chart_module 渲染函数用 stub 替换（sys.modules 注入），只返回固定小 HTML；
  - engine 全流程用 stub LLM 驱动 _run_async，tool 走真实 ChartTool.execute
    + 真实 ToolRegistry，验证 engine.py 现有 chart_html 读取逻辑能取到。

运行(目标解释器):
    python -m pytest tests/test_agent_phase1_b2.py -q
兼容:
    python -m unittest tests.test_agent_phase1_b2 -v
"""
import asyncio
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.engine import ReActAgent
from agent.llm import LLMResponse, ToolCall
from agent.tools.chart_tool import ChartTool
from agent.tools.registry import ToolRegistry

STUB_CHART_HTML = (
    '<div class="chart"><img src="data:image/png;base64,'
    'STUB_B2_BASE64_MARKER"/></div>'
)

CHART_ARGS = {
    "chart_type": "bar",
    "data": [
        {"stock_abbr": "白云山", "report_year": 2024,
         "report_period": "年报", "total_operating_revenue": 100.0}
    ],
    "title": "白云山营收趋势",
}


def _install_stub_chart_module():
    """用 stub 顶替真实 chart_module，避免真实 matplotlib 渲染。"""
    fake_module = mock.Mock()

    def _stub_chart(*args, **kwargs):
        return STUB_CHART_HTML

    for name in ("bar_chart", "line_chart", "hbar_chart", "pie_chart"):
        setattr(fake_module, name, _stub_chart)
    return mock.patch.dict(sys.modules, {"chart_module": fake_module})


class _FakeChartLLM:
    """按序返回: 1) chart 工具调用 2) 最终文本。"""

    def __init__(self):
        self.calls = 0
        self.recorded = []
        self.responses = [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(id="call_chart", name="chart",
                             arguments=CHART_ARGS)
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="已生成白云山营收趋势图表。"),
        ]

    def chat_with_tools(self, messages, tools, tool_choice="auto"):
        self.recorded.append(list(messages))
        resp = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return resp


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


def _build_engine_result(llm=None):
    """真实 ChartTool 注册进真实 ToolRegistry，stub LLM 驱动 engine 全流程。"""
    registry = ToolRegistry()
    registry.register(ChartTool())

    agent = object.__new__(ReActAgent)
    agent.max_iterations = 8
    agent.llm = llm or _FakeChartLLM()
    agent.tool_registry = registry
    agent.prompt_manager = _FakePromptManager()
    agent.tracer = _FakeTracer()

    return asyncio.run(
        agent._run_async(
            "白云山营收趋势",
            history=[],
            session_id="t2_test",
            last_context=None,
        )
    )


class TestChartHtmlContract(unittest.TestCase):
    """T2: chart_tool.execute 恢复 chart_html，engine.py 读取契约成立。"""

    def test_execute_returns_chart_html(self):
        """execute() 返回体必须含 chart_html，且与 run() 对齐。"""
        with _install_stub_chart_module():
            parsed = json.loads(asyncio.run(ChartTool().execute(**CHART_ARGS)))
        self.assertTrue(parsed.get("success"), "execute 应返回 success=True")
        self.assertIn("chart_html", parsed,
                      "execute 返回体缺少 chart_html（engine.py 读不到）")
        self.assertEqual(parsed["chart_html"], STUB_CHART_HTML)
        self.assertEqual(parsed["chart_type"], "bar")
        self.assertEqual(parsed["title"], CHART_ARGS["title"])

    def test_execute_error_path_aligned_with_run(self):
        """无 data 时 execute 与 run() 对齐：success=False + error。"""
        with _install_stub_chart_module():
            parsed = json.loads(asyncio.run(
                ChartTool().execute(chart_type="bar", data=[], title="空数据")
            ))
        self.assertFalse(parsed.get("success"))
        self.assertIn("error", parsed)

    def test_engine_reading_logic_gets_chart_html(self):
        """engine.py 现有读取逻辑（result_parsed -> .get("chart_html")）能取到。"""
        with _install_stub_chart_module():
            result = _build_engine_result()
        self.assertEqual(result.source, "agent")
        self.assertEqual(result.iterations, 2)
        self.assertIsNotNone(result.chart_html,
                             "AgentResult.chart_html 为 None，engine.py 读不到")
        self.assertEqual(result.chart_html, STUB_CHART_HTML)
        chart_tcs = [tc for tc in result.tool_calls_log
                     if tc.get("tool") == "chart"]
        self.assertEqual(len(chart_tcs), 1)
        parsed = chart_tcs[0]["result_parsed"]
        self.assertEqual(parsed["chart_html"], STUB_CHART_HTML)

    def test_llm_tool_message_has_no_chart_html(self):
        """注入下一轮 LLM 的 tool 消息不含 base64 chart_html（防回退）。"""
        llm = _FakeChartLLM()
        with _install_stub_chart_module():
            result = _build_engine_result(llm=llm)
        self.assertEqual(result.source, "agent")
        self.assertEqual(len(llm.recorded), 2,
                         "stub LLM 应被调用 2 次（工具轮 + 最终回答轮）")
        tool_msgs = [m for m in llm.recorded[1] if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        content = tool_msgs[0]["content"]
        self.assertNotIn("chart_html", content,
                         "LLM tool 消息仍携带 chart_html 字段")
        self.assertNotIn("base64", content,
                         "LLM tool 消息仍携带 base64 内容")
        self.assertNotIn("STUB_B2_BASE64_MARKER", content,
                         "LLM tool 消息仍携带 chart_html base64 内容")
        self.assertIn("success", content,
                      "tool 消息应保留结果要点供 LLM 决策")
        # 展示路径契约不破：result_parsed 仍持有完整 chart_html
        chart_tcs = [tc for tc in result.tool_calls_log
                     if tc.get("tool") == "chart"]
        self.assertEqual(chart_tcs[0]["result_parsed"]["chart_html"],
                         STUB_CHART_HTML)


if __name__ == "__main__":
    unittest.main(verbosity=2)
