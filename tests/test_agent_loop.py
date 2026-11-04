# -*- coding: utf-8 -*-
"""引擎循环接线（D1+D2+D3）端到端冒烟 — stub LLM + fake registry，不碰真实服务。

验证核心闭环: 同一工具同参数连续两轮 → 第二轮被 CallMemory 拦截（真实执行仅 1 次）
→ 本轮全拦截触发 no_new_info → force_answer → 下一轮 tool_choice="none" 收敛作答
（source=agent，而非撞 max_iterations 致歉）。
"""
import asyncio
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.engine import ReActAgent
from agent.llm import LLMResponse, ToolCall


class _FakeLLM:
    """按序返回: 1) 查工具 2) 同样查工具（应被拦截） 3) 最终作答。"""

    def __init__(self):
        self.calls = 0
        self.responses = [
            LLMResponse(content=None, finish_reason="tool_calls", tool_calls=[
                ToolCall(id="c1", name="sql_query",
                         arguments={"question": "云南白药净利润"})]),
            LLMResponse(content=None, finish_reason="tool_calls", tool_calls=[
                ToolCall(id="c2", name="sql_query",
                         arguments={"question": "云南白药净利润"})]),
            LLMResponse(content="云南白药 2024 年净利润为 40 亿元。"),
        ]

    def chat_with_tools(self, messages, tools, tool_choice="auto"):
        resp = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return resp


class _ScriptedLLM:
    """按脚本返回响应，并记录每次调用的 tool_choice 与 messages（供断言）。"""

    def __init__(self, responses):
        self.responses = responses
        self.calls = 0
        self.tool_choices = []
        self.seen_messages = []

    def chat_with_tools(self, messages, tools, tool_choice="auto"):
        self.tool_choices.append(tool_choice)
        self.seen_messages.append(list(messages))
        resp = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return resp


class _FakeRegistry:
    """返回与真实 sql_query 同构的契约结果，并统计真实执行次数。"""

    def __init__(self):
        self.execute_count = 0

    async def execute(self, name, **kwargs):
        self.execute_count += 1
        return json.dumps({
            "success": True,
            "data": [{"净利润": 400000, "stock_abbr": "云南白药", "report_year": 2024}],
            "companies": ["云南白药"],
            "indicators": ["净利润"],
            "years": ["2024"],
        }, ensure_ascii=False)

    def to_openai_schemas(self):
        return []

    def get_tool_descriptions(self):
        return ""


class _FailingRegistry(_FakeRegistry):
    """永远返回 field_not_found 形态的失败结果（D3 错误指引的触发源）。"""

    async def execute(self, name, **kwargs):
        self.execute_count += 1
        return json.dumps({
            "success": False,
            "error": "数据库中不存在字段「营收对比」",
        }, ensure_ascii=False)


class _FakePromptManager:
    def build_system_prompt(self, **kw):
        return "[system]"

    def build_context_hint(self, **kw):
        return ""


class _FakeTracer:
    class _Ctx:
        def __enter__(self):
            return None

        def __exit__(self, *a):
            return False

    def trace(self, *a, **k):
        return self._Ctx()

    def span(self, *a, **k):
        return self._Ctx()

    def add_event(self, *a, **k):
        pass

    def to_dict(self):
        return {"spans": []}


def _build(llm=None, registry=None):
    agent = object.__new__(ReActAgent)
    agent.max_iterations = 8
    agent.llm = llm or _FakeLLM()
    agent.tool_registry = registry or _FakeRegistry()
    agent.prompt_manager = _FakePromptManager()
    agent.tracer = _FakeTracer()
    return agent


class TestEngineLoop(unittest.TestCase):
    """D1+D2 接线冒烟：去重拦截生效 + 收敛作答。"""

    def test_dedup_and_converge(self):
        agent = _build()
        result = asyncio.run(agent._run_async(
            "云南白药净利润", history=[], session_id="t", last_context=None))

        self.assertEqual(agent.tool_registry.execute_count, 1,
                         "同参数第二轮应被 CallMemory 拦截，真实执行仅 1 次")
        self.assertEqual(result.source, "agent",
                         "应正常收敛作答，而非撞 max_iterations 致歉")
        self.assertLess(result.iterations, 8)
        self.assertIn("净利润", result.answer)

        dup_logs = [tc for tc in result.tool_calls_log if tc.get("dup")]
        self.assertEqual(len(dup_logs), 1, "第二轮重复调用应记为 dup 留痕")

        # D8 指标聚合：汇总值应与流水一致
        self.assertIsNotNone(result.metrics)
        self.assertEqual(result.metrics["dedup_hits"], 1)
        self.assertEqual(result.metrics["tool_calls_executed"], 1)
        self.assertEqual(result.metrics["converged_reason"],
                         "force_answer:no_new_info")

    def test_failed_retry_then_blocked(self):
        """失败重试 1 次后被拦截：同参数第 3 次调用不再真实执行。

        链路：iter1 执行失败 → iter2 允许重试（再失败）→ iter3 同参调用
        被 CallMemory 硬拦截（dup-failed）→ 全轮拦截触发 no_new_info
        → force_answer 收敛作答。
        """
        llm = _ScriptedLLM([
            LLMResponse(content=None, finish_reason="tool_calls", tool_calls=[
                ToolCall(id="c1", name="sql_query", arguments={"question": "X"})]),
            LLMResponse(content=None, finish_reason="tool_calls", tool_calls=[
                ToolCall(id="c2", name="sql_query", arguments={"question": "X"})]),
            LLMResponse(content=None, finish_reason="tool_calls", tool_calls=[
                ToolCall(id="c3", name="sql_query", arguments={"question": "X"})]),
            LLMResponse(content="库中暂无该口径数据。"),
        ])
        registry = _FailingRegistry()
        agent = _build(llm=llm, registry=registry)
        result = asyncio.run(agent._run_async("X", [], "t", None))

        self.assertEqual(registry.execute_count, 2,
                         "失败调用只允许重试 1 次，第 3 次应被拦截不再执行")
        dup_failed = [tc for tc in result.tool_calls_log
                      if tc.get("result_preview") == "[dup-failed]"]
        self.assertEqual(len(dup_failed), 1, "第 3 次同参调用应记为 dup-failed")
        self.assertEqual(result.source, "agent",
                         "拦截后应触发 no_new_info 强制收口，而非撞上限")
        self.assertEqual(llm.tool_choices[-1], "none")
        # D8 指标聚合
        self.assertEqual(result.metrics["dedup_hits"], 1)
        self.assertEqual(result.metrics["tool_calls_executed"], 2)
        self.assertTrue(result.metrics["force_answer_triggered"])
        self.assertEqual(result.metrics["force_answer_reason"], "no_new_info")
        self.assertEqual(result.metrics["converged_reason"],
                         "force_answer:no_new_info")

    def test_error_guidance_injected(self):
        """工具失败时 D3 错误指引应拼进 tool 消息，随下一轮发给 LLM。"""
        llm = _ScriptedLLM([
            LLMResponse(content=None, finish_reason="tool_calls", tool_calls=[
                ToolCall(id="c1", name="sql_query",
                         arguments={"question": "云南白药营收对比"})]),
            LLMResponse(content="已换用可用字段重新查询。"),
        ])
        agent = _build(llm=llm, registry=_FailingRegistry())
        result = asyncio.run(agent._run_async("云南白药营收对比", [], "t", None))

        self.assertEqual(result.source, "agent")
        second_call_msgs = llm.seen_messages[1]
        tool_msgs = [m for m in second_call_msgs if m.get("role") == "tool"]
        self.assertTrue(tool_msgs, "第二轮应携带上一轮的工具结果消息")
        self.assertIn("【字段不存在】", tool_msgs[-1]["content"],
                      "D3 错误指引应拼进 tool 消息（field_not_found → 换字段）")

    def test_force_answer_tool_calls_fallback(self):
        """force_answer 后模型仍返回 tool_calls → 不再执行，直接兜底作答。

        tool_choice="none" 只是"请求"，模型仍可能返回工具调用（罕见分支）；
        引擎必须拦截执行并用 content/兜底文案收口，否则去重与强制收口形同虚设。
        """
        llm = _ScriptedLLM([
            LLMResponse(content=None, finish_reason="tool_calls", tool_calls=[
                ToolCall(id="c1", name="sql_query",
                         arguments={"question": "云南白药净利润"})]),
            LLMResponse(content=None, finish_reason="tool_calls", tool_calls=[
                ToolCall(id="c2", name="sql_query",
                         arguments={"question": "云南白药净利润"})]),
            # 第三轮（tool_choice="none"）模型仍返回工具调用、content 为空
            LLMResponse(content="", finish_reason="tool_calls", tool_calls=[
                ToolCall(id="c3", name="chart", arguments={"title": "x"})]),
        ])
        registry = _FakeRegistry()
        agent = _build(llm=llm, registry=registry)
        result = asyncio.run(agent._run_async("云南白药净利润", [], "t", None))

        self.assertEqual(registry.execute_count, 1,
                         "tool_choice=none 下返回的工具调用不得再执行")
        self.assertEqual(llm.tool_choices[-1], "none",
                         "第三轮应以 tool_choice=none 强制作答")
        self.assertEqual(result.source, "agent")
        self.assertEqual(result.answer,
                         "抱歉，基于已收集的信息我暂时无法给出准确结论。")
        # D8 指标聚合
        self.assertEqual(result.metrics["converged_reason"],
                         "force_answer_tool_calls")
        self.assertEqual(result.metrics["dedup_hits"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
