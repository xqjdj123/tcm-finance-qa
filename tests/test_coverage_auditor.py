# -*- coding: utf-8 -*-
"""CoverageAuditor（阶段2）契约单测 — stub LLM + fake registry，不碰真实服务。

覆盖六条契约：
  1. 审计通过 → 正常收敛（audit_status=passed，不打回）
  2. 审计发现未答 → 注入补救指引、回主循环再作答（bounced, audit_rounds=1）
  3. force_answer 时 → 只记录不打回（force_not_bounced，Evaluator 一票权不被架空）
  4. 审计器异常 → fail-open（error，按原答案收敛）
  5. max_rounds 封顶 → 不打回第二次，绝不死循环
  6. 真实审计器：容忍 ```json 围栏解析；注入文案区分「已查未答 vs 未查」；
     非 JSON 输出 → fail-open
"""
import asyncio
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.engine import ReActAgent
from agent.coverage import CoverageAuditor, CoverageAudit
from agent.llm import LLMResponse, ToolCall


# ============================================================
# 假件
# ============================================================
class _ScriptedLLM:
    """按脚本返回响应，并记录每次调用的 tool_choice / messages。"""

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


class _Registry:
    def __init__(self):
        self.execute_count = 0

    async def execute(self, name, **kwargs):
        self.execute_count += 1
        return json.dumps({
            "success": True,
            "data": [{"净利润": 400000, "stock_abbr": "云南白药"}],
            "indicators": ["净利润"],
        }, ensure_ascii=False)

    def to_openai_schemas(self):
        return []

    def get_tool_descriptions(self):
        return ""


class _PromptManager:
    def build_system_prompt(self, **kw):
        return "[system]"

    def build_context_hint(self, **kw):
        return ""


class _Tracer:
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


class _FakeAuditor:
    """按脚本返回审计结论（耗尽后复用最后一个），并记录调用次数。"""

    def __init__(self, results, max_rounds=1, min_tool_calls=1):
        self.results = results
        self.max_rounds = max_rounds
        self.min_tool_calls = min_tool_calls
        self.calls = 0
        self.audited_answers = []

    def should_audit(self, *, force_answer, audit_rounds, tool_calls_log):
        if audit_rounds >= self.max_rounds:
            return False
        executed = [tc for tc in (tool_calls_log or []) if not tc.get("dup")]
        return len(executed) >= self.min_tool_calls

    def audit(self, question, tool_calls_log, answer):
        self.audited_answers.append(answer)
        r = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        return r


class _StubChatLLM:
    """只实现 chat() 的桩（真实审计器走 LLMClient.chat）。"""

    def __init__(self, reply):
        self.reply = reply
        self.seen = []

    def chat(self, system_prompt, user_message, temperature=None):
        self.seen.append((system_prompt, user_message, temperature))
        return self.reply


def _pass():
    return CoverageAudit(ok=True, missing=[], advice="")


def _missing(*texts, status="not_looked"):
    return CoverageAudit(ok=True,
                         missing=[{"text": t, "status": status} for t in texts],
                         advice="[覆盖审计] 请补齐：" + "、".join(texts))


def _build(llm, registry=None, auditor=None, max_iterations=8):
    agent = object.__new__(ReActAgent)
    agent.max_iterations = max_iterations
    agent.llm = llm
    agent.tool_registry = registry or _Registry()
    agent.prompt_manager = _PromptManager()
    agent.tracer = _Tracer()
    agent.coverage_auditor = auditor
    return agent


def _sql_call(cid="c1"):
    return LLMResponse(content=None, finish_reason="tool_calls", tool_calls=[
        ToolCall(id=cid, name="sql_query", arguments={"question": "云南白药净利润"})])


def _answer(text):
    return LLMResponse(content=text)


# ============================================================
# 引擎集成契约
# ============================================================
class TestCoverageAuditorContract(unittest.TestCase):

    def test_audit_pass_converges(self):
        """审计通过 → 正常收敛，不打回、迭代数不变。"""
        llm = _ScriptedLLM([_sql_call(), _answer("云南白药 2024 净利润 40 亿元。")])
        auditor = _FakeAuditor([_pass()])
        agent = _build(llm, auditor=auditor)
        result = asyncio.run(agent._run_async("云南白药净利润", [], "t", None))

        self.assertEqual(result.answer, "云南白药 2024 净利润 40 亿元。")
        self.assertEqual(result.iterations, 2)
        self.assertEqual(result.metrics["audit_status"], "passed")
        self.assertEqual(result.metrics["audit_rounds"], 0)
        self.assertEqual(auditor.calls, 1)

    def test_audit_bounce_then_complete(self):
        """审计发现未答 → 注入补救指引 + 回主循环 → 下一轮给出完整答案。"""
        llm = _ScriptedLLM([_sql_call(), _answer("半截答案"),
                            _answer("完整答案：净利润 40 亿元，净利率 11.9%。")])
        auditor = _FakeAuditor([_missing("净利率")])
        agent = _build(llm, auditor=auditor)
        result = asyncio.run(agent._run_async("云南白药净利润和净利率", [], "t", None))

        self.assertEqual(result.answer, "完整答案：净利润 40 亿元，净利率 11.9%。",
                         "被审计打回后应以补答后的完整答案收敛")
        self.assertEqual(result.iterations, 3)
        self.assertEqual(result.metrics["audit_status"], "bounced")
        self.assertEqual(result.metrics["audit_rounds"], 1)
        self.assertEqual(result.metrics["audit_missing_count"], 1)
        self.assertEqual(auditor.calls, 1,
                         "max_rounds=1 → 打回一次后不再复审（补答直接收敛，避免无限复审）")
        # 补救指引必须真的进了下一轮发给 LLM 的消息里
        last_msgs = llm.seen_messages[-1]
        joined = "\n".join(m.get("content", "") for m in last_msgs
                           if m.get("role") == "user")
        self.assertIn("[覆盖审计]", joined)
        self.assertIn("净利率", joined)

    def test_force_answer_not_bounced(self):
        """force_answer（Evaluator 一票强制收口）时：审计只记录、不打回。"""
        llm = _ScriptedLLM([_sql_call("c1"), _sql_call("c2"),
                            _answer("已强制收口给出的答案。")])
        auditor = _FakeAuditor([_missing("同比变化")])   # 即便发现未答，也不得打回
        agent = _build(llm, auditor=auditor)
        result = asyncio.run(agent._run_async("云南白药净利润", [], "t", None))

        self.assertEqual(llm.tool_choices[-1], "none", "应收敛于 tool_choice=none 的强制作答")
        self.assertEqual(result.answer, "已强制收口给出的答案。")
        self.assertEqual(result.iterations, 3)
        self.assertEqual(result.metrics["audit_status"], "force_not_bounced")
        self.assertEqual(result.metrics["audit_missing_count"], 1)
        self.assertEqual(result.metrics["audit_rounds"], 0, "未打回 → 审计轮次不增")
        self.assertEqual(auditor.calls, 1)

    def test_auditor_error_fail_open(self):
        """审计器异常 → fail-open：按原答案正常收敛，不因审计故障拖垮主链路。"""
        llm = _ScriptedLLM([_sql_call(), _answer("原答案。")])
        auditor = _FakeAuditor([CoverageAudit(ok=False, error="boom")])
        agent = _build(llm, auditor=auditor)
        result = asyncio.run(agent._run_async("云南白药净利润", [], "t", None))

        self.assertEqual(result.answer, "原答案。")
        self.assertEqual(result.iterations, 2)
        self.assertEqual(result.metrics["audit_status"], "error")
        self.assertEqual(result.metrics["audit_rounds"], 0)

    def test_max_rounds_cap_no_deadloop(self):
        """max_rounds 封顶：审计永远判"没答全"也只打回一次，绝不死循环。"""
        llm = _ScriptedLLM([_sql_call(), _answer("a1"), _answer("a2")])
        auditor = _FakeAuditor([_missing("永远未答")], max_rounds=1)
        agent = _build(llm, auditor=auditor)
        result = asyncio.run(agent._run_async("多子问题", [], "t", None))

        self.assertEqual(result.metrics["audit_rounds"], 1, "只允许打回一次")
        self.assertEqual(result.metrics["audit_status"], "bounced",
                         "run 级状态应记『打回过』，不被末轮的 skipped 覆盖")
        self.assertEqual(auditor.calls, 1, "达上限后不再发起审计")
        self.assertEqual(result.answer, "a2")
        self.assertEqual(result.iterations, 3)

    def test_min_tool_calls_gate(self):
        """工具调用不足 min_tool_calls → 不审计（省一次 LLM 调用）。"""
        llm = _ScriptedLLM([_sql_call(), _answer("答案")])
        auditor = _FakeAuditor([_missing("x")], min_tool_calls=2)
        agent = _build(llm, auditor=auditor)
        result = asyncio.run(agent._run_async("简单问题", [], "t", None))

        self.assertEqual(result.metrics["audit_status"], "skipped")
        self.assertEqual(auditor.calls, 0, "不足门槛不应发起审计")


# ============================================================
# 真实审计器：解析健壮性 + 两种「未答」措辞区分
# ============================================================
class TestCoverageAuditorUnit(unittest.TestCase):

    def test_parses_fenced_json_and_advice_distinguishes(self):
        llm = _StubChatLLM(
            "```json\n"
            '{"missing":['
            '{"text":"同比变化","status":"looked_not_answered"},'
            '{"text":"研发占比","status":"not_looked"}]}\n'
            "```")
        auditor = CoverageAuditor(llm)
        aud = auditor.audit(
            "问题", [{"tool": "sql_query", "arguments": {"question": "x"},
                      "result_parsed": {"success": True, "data": [{"净利润": 1}]}}],
            "答案")

        self.assertTrue(aud.ok)
        self.assertEqual(len(aud.missing), 2)
        self.assertIn("不要重新查询", aud.advice, "已查未答 → 引导直接补答、勿重查")
        self.assertIn("尚未查询", aud.advice, "未查 → 引导先取数再答")
        # temperature 必须为 0（可复现）
        self.assertEqual(llm.seen[-1][2], 0.0)

    def test_non_json_output_fail_open(self):
        auditor = CoverageAuditor(_StubChatLLM("这根本不是 JSON，只是胡言乱语。"))
        aud = auditor.audit("q", [{"tool": "sql_query", "arguments": {}}], "答案")
        self.assertFalse(aud.ok)
        self.assertTrue(aud.error)

    def test_unknown_status_defaults_to_not_looked(self):
        llm = _StubChatLLM('{"missing":[{"text":"某子问题","status":"whatever"}]}')
        aud = CoverageAuditor(llm).audit("q", [{"tool": "sql_query", "arguments": {}}], "a")
        self.assertTrue(aud.ok)
        self.assertEqual(aud.missing[0]["status"], "not_looked",
                         "未知状态 → 保守按『未查』（引导先查最安全）")

    def test_llm_error_string_fail_open(self):
        auditor = CoverageAuditor(_StubChatLLM("[LLM Error] 请求超时"))
        aud = auditor.audit("q", [{"tool": "sql_query", "arguments": {}}], "a")
        self.assertFalse(aud.ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
