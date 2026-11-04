# -*- coding: utf-8 -*-
"""T9 (A1) event loop 加固 — 并发回归单测（stub LLM/工具，无真实 LLM/DB）。

背景: engine.run() 此前无条件 asyncio.run(...)，每当调用线程已处于运行中的
      event loop（async 上下文 / 多线程并发）时，会抛
      "asyncio.run() cannot be called from a running event loop"。
修法: engine.run() 只在"当前线程无运行中的 loop"时走 asyncio.run；
      当前线程已有运行中的 loop 时，调度到进程级复用的单一 event loop
      （独立后台线程）执行并阻塞等待，无 loop 冲突、不新建 loop。

本测试全链路驱动 engine.run() 同步入口（含工具调用->结果注入->最终回答），
覆盖三类场景：
  1. 单线程顺序调用（debug 单线程不回退，返回契约不变）；
  2. 多线程同时调 engine.run（Flask 默认多线程模型）N 次，全部成功；
  3. 每个线程已处于自己运行中的 event loop（async 上下文）时调用 run()，
     这是修复前的确定性崩溃场景，修复后必须全部成功。

运行:
    python -m pytest tests/test_agent_phase1_a1.py -q
"""
import asyncio
import json
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.engine import ReActAgent, AgentResult
from agent.llm import LLMResponse, ToolCall


class _FakeLLM:
    """线程安全的脚本 LLM：每个问题第一次调用返回一次 sql_query 工具调用，
    后续调用返回最终文本。计数按问题内容隔离，互不串扰。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._steps = {}

    def chat_with_tools(self, messages, tools=None, tool_choice="auto"):
        q = None
        for msg in messages:
            if msg.get("role") == "user":
                q = str(msg.get("content", ""))[:20]
                break
        with self._lock:
            step = self._steps.get(q, 0)
            self._steps[q] = step + 1
        if step == 0:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(id="call_" + q, name="sql_query",
                             arguments={"question": q})
                ],
                finish_reason="tool_calls",
            )
        return LLMResponse(content="已完成:" + q, finish_reason="stop")


class _FakeRegistry:
    """与真实 sql_query 工具同构的 JSON 返回；sleep(0) 制造真实挂起点。"""

    async def execute(self, name, **kwargs):
        await asyncio.sleep(0)
        body = {
            "success": True,
            "data": [{"stock_abbr": str(name), "report_year": 2024,
                      "question": str(kwargs.get("question", name))}],
            "answer": str(kwargs.get("question", name)),
        }
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
    """最小 tracer：仅要求 engine 用到的方法可空跑（无锁无共享状态）。"""

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


def _build_agent() -> ReActAgent:
    """构造带 stub LLM/注册表/prompt/tracer 的引擎实例，走真实 run() 入口。"""
    agent = object.__new__(ReActAgent)
    agent.max_iterations = 8
    agent.llm = _FakeLLM()
    agent.tool_registry = _FakeRegistry()
    agent.prompt_manager = _FakePromptManager()
    agent.tracer = _FakeTracer()
    return agent


class TestEngineRunEventLoop(unittest.TestCase):
    """A1: run() 的 event loop 复用 / 条件化行为。"""

    def test_run_return_contract_unchanged_single_thread(self):
        """同步返回契约不变；debug 单线程连续调用不回退。"""
        agent = _build_agent()
        r1 = agent.run("白云山2024年净利润是多少", session_id="seq1")
        self.assertIsInstance(r1, AgentResult)
        self.assertEqual(r1.source, "agent")
        self.assertEqual(r1.iterations, 2)
        self.assertTrue(r1.answer.startswith("已完成:"))
        self.assertEqual(len(r1.tool_calls_log), 1)
        self.assertEqual(r1.tool_calls_log[0]["tool"], "sql_query")
        self.assertTrue(r1.sql_data, "sql_query 工具产物必须回填 sql_data")

        r2 = agent.run("白云山2025年营收呢", session_id="seq2")
        self.assertIsInstance(r2, AgentResult)
        self.assertEqual(r2.source, "agent")
        self.assertNotEqual(r2.answer, r1.answer, "两次查询必须各回各的答案")

    def test_multithread_concurrent_run_all_success(self):
        """Flask 多线程模型：多线程同时调 run() N 次，无冲突且全部成功。"""
        n_threads = 12
        agent = _build_agent()
        barrier = threading.Barrier(n_threads)
        results = [None] * n_threads
        errors = []

        def worker(idx):
            try:
                barrier.wait(timeout=30)
                results[idx] = agent.run(
                    "并发问题%d:白云山2024年净利润是多少" % idx,
                    session_id="thr%d" % idx,
                )
            except Exception as exc:  # pragma: no cover - 失败时留证据
                errors.append((idx, repr(exc)))

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        self.assertFalse(errors, "并发调用出现异常: %s" % errors)
        for i, res in enumerate(results):
            self.assertIsInstance(res, AgentResult)
            self.assertEqual(res.source, "agent",
                             "线程%d 未成功" % i)
            self.assertEqual(len(res.tool_calls_log), 1)
            self.assertTrue(res.sql_data, "线程%d 工具链路未走通" % i)

    def test_run_inside_running_loop_all_success(self):
        """修复前确定性崩溃场景：调用线程已有运行中的 event loop。
        修复后 run() 必须不抛 asyncio.run 冲突，全部成功。"""
        n_threads = 6
        agent = _build_agent()
        barrier = threading.Barrier(n_threads)
        results = [None] * n_threads
        errors = []

        def worker(idx):
            async def caller():
                # 同步 run() 在 async 上下文内被调用（修复前在此抛 RuntimeError）
                return agent.run("异步上下文问题%d:白云山净利润" % idx,
                                 session_id="async%d" % idx)

            try:
                barrier.wait(timeout=30)
                loop = asyncio.new_event_loop()
                try:
                    results[idx] = loop.run_until_complete(caller())
                finally:
                    loop.close()
            except Exception as exc:
                errors.append((idx, repr(exc)))

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        self.assertFalse(
            errors,
            "运行 loop 内调用 run() 出现 event loop 冲突: %s" % errors,
        )
        for i, res in enumerate(results):
            self.assertIsInstance(res, AgentResult)
            self.assertEqual(res.source, "agent")
            self.assertTrue(res.answer.startswith("已完成:"))
            self.assertTrue(res.sql_data)


if __name__ == "__main__":
    unittest.main(verbosity=2)