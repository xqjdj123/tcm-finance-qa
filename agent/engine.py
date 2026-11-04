# -*- coding: utf-8 -*-
"""
ReAct Agent 引擎 — 单次理解、动态工具调用
替换 FinanceAgent.query() 的双层 pipeline+agent 架构

RQagentPro 风格 ReAct 循环:
  用户问题 → 构建消息(系统Prompt + 历史 + 上下文注入)
           → [ReAct 循环]
              LLM 决定工具 → 执行 → 结果注入 → LLM 再决定
           → 输出校验 → 返回

核心改进:
  1. 不强制预处理 (pipeline.understand() 不再是必经之路)
  2. 不硬编码 INTENT_PLANS (LLM 自主选工具)
  3. 上下文注入替代关键词追问检测
  4. 单一路径, 无 _run_single / _run_single_with_und 重复
"""
import json
import re
import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent.llm import LLMClient, LLMResponse, ToolCall as LLMToolCall
from agent.tools.registry import get_tool_registry, ensure_tools_registered
from agent.prompt_manager import get_prompt_manager
from agent.tracer import SpanTracer, get_tracer
from agent.call_memory import CallMemory
from agent.evaluator import Evaluator
from agent.coverage import CoverageAuditor
from agent.recovery import classify_error, render_guidance


# ============================================================
# 同步桥接：event loop 复用（并发安全）
# ============================================================

_loop_guard = threading.Lock()
_shared_loop: Optional[asyncio.AbstractEventLoop] = None
_shared_loop_thread: Optional[threading.Thread] = None


def _get_shared_loop() -> asyncio.AbstractEventLoop:
    """懒创建并返回进程级复用的 event loop（常驻独立后台线程）。

    仅在"当前线程已有运行中的 event loop、无法再 asyncio.run()"的场景使用；
    run() 的常规同步路径（如 Flask 请求线程，当前线程无运行 loop）不经过这里。

    loop 必须在后台线程内部创建并运行（同一线程创建+运行），避免
    Windows ProactorEventLoop 等对"跨线程创建/运行"敏感的实现出问题。
    """
    global _shared_loop, _shared_loop_thread
    if _shared_loop is None:
        with _loop_guard:
            if _shared_loop is None:
                ready = threading.Event()
                holder = {}

                def _loop_worker():
                    loop = asyncio.new_event_loop()
                    holder["loop"] = loop
                    ready.set()
                    loop.run_forever()

                worker = threading.Thread(
                    target=_loop_worker,
                    name="agent-shared-loop",
                    daemon=True,
                )
                worker.start()
                ready.wait()
                _shared_loop = holder["loop"]
                _shared_loop_thread = worker
    return _shared_loop


def _run_coro_sync(coro) -> Any:
    """以同步方式执行协程，且不引发 event loop 冲突。

    规则：
      1. 当前线程无运行中的 event loop（Flask 默认线程 / 普通调用）：
         用 asyncio.run —— 仅在"当前线程无运行 loop"时才新建并运行 loop；
      2. 当前线程已有运行中的 event loop（已在 async 上下文）：
         不新建 loop，改为调度到进程级复用的单一 loop（独立后台线程）上
         执行并阻塞等待结果——跨线程安全，无 asyncio.run 冲突。
    """
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    shared_loop = _get_shared_loop()
    if running_loop is shared_loop:
        raise RuntimeError(
            "engine.run() 不允许在共享 event loop 所在线程内同步调用"
            "（会死锁）；异步代码请直接 await ReActAgent._run_async(...)"
        )
    return asyncio.run_coroutine_threadsafe(coro, shared_loop).result()


# ============================================================
# 数据结构
# ============================================================

@dataclass
class AgentResult:
    """ReAct Agent 运行结果"""
    answer: str = ""
    tool_calls_log: List[Dict] = field(default_factory=list)
    iterations: int = 0
    source: str = "agent"         # "agent" | "fallback" | "max_iterations" | "error"
    chart_html: Optional[str] = None
    sql_data: Optional[List[Dict]] = None
    session_id: str = ""
    trace: Optional[Dict] = None
    metrics: Optional[Dict] = None  # D8 指标聚合（dedup_hits / converged_reason 等）


# 注入 LLM 的工具结果字符预算。
# 过小（如 200）会把多行查询结果截成半句，模型误判"没查到"并反复重查同一工具，
# 最终耗尽迭代次数；过大则上下文膨胀。4000 字符 ≈ 1000 token，可覆盖数十行明细。
# 2026-09 接线：改为读 config.yaml `agent.tool_result_budget`。
# 此前该键是“配置孤儿”——config.yaml 里写了却没人读，engine 另写死一份常量，
# 同一份知识两处拷贝必然漂移（答辩问“你配置里的预算生效了吗”会答不上来）。
# 现在：配置为准，常量降为兜底默认值。
_DEFAULT_TOOL_RESULT_BUDGET = 4000


def _load_tool_result_budget() -> int:
    try:
        from agent.config import get_config
        cfg = get_config()
        cfg.load()
        v = cfg.get("agent.tool_result_budget")
        if v is not None:
            return int(v)
    except Exception as e:
        print(f"[engine] 读取 agent.tool_result_budget 失败，回退默认 "
              f"{_DEFAULT_TOOL_RESULT_BUDGET}: {e}")
    return _DEFAULT_TOOL_RESULT_BUDGET


TOOL_RESULT_BUDGET = _load_tool_result_budget()


def _tool_message_content(result_str: str, result_data: Any) -> str:
    """构造注入下一轮 LLM 的 tool 消息内容。

    chart_html 是仅供前端展示的 base64 大字段，不进入 LLM 上下文；
    其余结果按 TOOL_RESULT_BUDGET 截断，并在截断处显式告知模型
    "结果已截断 + 原始长度"，避免模型把"没看全"误判成"没查到"。
    """
    if isinstance(result_data, dict) and "chart_html" in result_data:
        summary = {k: v for k, v in result_data.items() if k != "chart_html"}
        result_str = json.dumps(summary, ensure_ascii=False)

    if len(result_str) <= TOOL_RESULT_BUDGET:
        return result_str
    return (result_str[:TOOL_RESULT_BUDGET]
            + f"...[结果已截断：原始长度 {len(result_str)} 字符，"
              f"以上为前 {TOOL_RESULT_BUDGET} 字符]")


def _build_metrics(*, iterations: int, tool_calls_log: List[Dict],
                   memory: Optional[CallMemory], force_answer: bool,
                   force_answer_reason: Optional[str],
                   converged_reason: str, source: str,
                   audit_rounds: int = 0, audit_status: str = "n/a",
                   audit_missing_count: int = 0,
                   audit_missing: Optional[List[str]] = None) -> Dict:
    """D8 指标聚合：一次运行的可观测指标汇总。

    trace 只记流水（逐 span），此前 dedup_hits / 收敛原因要靠人肉翻日志统计；
    这里在每条返回路径统一汇总，只读既有流水（tool_calls_log / CallMemory），
    不改变任何运行行为。评测/报表可直接取 AgentResult.metrics。

    converged_reason 取值：
      final_answer                LLM 在自然轮直接作答（未触发强制收口）
      force_answer:<Evaluator原因> 触发强制收口后作答（no_new_info / soft_limit）
      force_answer_tool_calls     tool_choice="none" 下模型仍返回工具调用的兜底
      max_iterations              撞硬上限
      llm_error / empty_response  异常路径
    """
    dup = sum(1 for tc in tool_calls_log if tc.get("dup"))
    return {
        "iterations": iterations,
        "tool_calls_total": len(tool_calls_log),
        "tool_calls_executed": len(tool_calls_log) - dup,
        "dedup_hits": memory.repeated_count() if memory is not None else dup,
        "force_answer_triggered": bool(force_answer),
        "force_answer_reason": force_answer_reason,
        "converged_reason": converged_reason,
        "source": source,
        # 阶段2 覆盖审计（CoverageAuditor）观测；n/a = 该路径未触发审计：
        #   audit_status ∈ n/a | passed | bounced | force_not_bounced | error | skipped
        # audit_missing = 审计器判定的未答子问题文本（截断留档，供"打回是否合理"复核）
        "audit_rounds": audit_rounds,
        "audit_status": audit_status,
        "audit_missing_count": audit_missing_count,
        "audit_missing": list(audit_missing or []),
    }


# ============================================================
# ReAct Agent
# ============================================================

class ReActAgent:
    """单 Pass ReAct 财务分析 Agent"""

    # 类级默认值：即使 object.__new__ 绕过 __init__（既有测试用），
    # 这些属性仍可用；__init__ 会用 config/config.yaml 的值覆盖它们。
    soft_iteration_limit = 5
    call_dedup = True
    failed_call_max_retry = 1
    evaluator_enabled = True
    coverage_auditor = None     # 阶段2：None = 未启用（object.__new__ 测试路径）

    def __init__(self, llm_client: LLMClient = None):
        from agent.config import (
            MAX_STEPS, SOFT_ITERATION_LIMIT, CALL_DEDUP,
            FAILED_CALL_MAX_RETRY, EVALUATOR_ENABLED,
            get_coverage_audit_config,
        )
        self.max_iterations = MAX_STEPS
        self.soft_iteration_limit = SOFT_ITERATION_LIMIT
        self.call_dedup = CALL_DEDUP
        self.failed_call_max_retry = FAILED_CALL_MAX_RETRY
        self.evaluator_enabled = EVALUATOR_ENABLED
        self.history_turns = 3  # 保留最近3轮对话

        self.llm = llm_client or LLMClient()
        self.tool_registry = ensure_tools_registered()
        if len(self.tool_registry) == 0:
            raise RuntimeError("工具注册失败：registry 为空（ensure_tools_registered 后仍无工具）")
        self.prompt_manager = get_prompt_manager()
        self.tracer = get_tracer()

        # 阶段2：覆盖审计器（收敛前独立验收）。被禁用时为 None →
        # 主循环读到 None 即不做任何审计，行为与接线前逐字一致。
        audit_cfg = get_coverage_audit_config()
        self.coverage_auditor = (
            CoverageAuditor(
                self.llm,
                max_rounds=int(audit_cfg["max_rounds"]),
                min_tool_calls=int(audit_cfg["min_tool_calls"]),
            ) if audit_cfg["enabled"] else None)

        # 延迟导入 Pipeline（NER + Model2 工具的底层依赖）
        self._pipeline = None

    def _get_pipeline(self):
        """懒加载 pipeline（SQL 生成、字段匹配等核心能力仍需要）"""
        if self._pipeline is None:
            import sys
            import os
            sys.path.insert(0, os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "models", "model2_question_understanding"
            ))
            from pipeline import FinancialQAPipeline
            self._pipeline = FinancialQAPipeline()
        return self._pipeline

    # ================================================================
    # 主入口 (同步 — 兼容 Flask)
    # ================================================================

    def run(self, question: str, history: List[Dict] = None,
            session_id: str = "default",
            last_context: Dict = None) -> AgentResult:
        """
        同步主入口

        参数:
          question: 用户当前问题
          history: 对话历史 [{"role":"user/assistant", "content":"..."}]
          session_id: 会话 ID
          last_context: 上一轮的上下文（用于追问意图继承）
            {"company": "白云山", "indicator": "净利润", "year": "2024",
             "answer_summary": "白云山2024年净利润为45.2亿元"}

        返回:
          AgentResult

        Event loop 策略：当前线程无运行中的 loop 时走 asyncio.run；
        已在 async 上下文（有运行中的 loop）时调度到进程级复用的单一 loop，
        避免"asyncio.run() cannot be called from a running event loop"冲突。
        """
        return _run_coro_sync(
            self._run_async(question, history, session_id, last_context)
        )

    async def _run_async(self, question: str, history: List[Dict],
                         session_id: str, last_context: Dict) -> AgentResult:
        """异步实现"""

        with self.tracer.trace(session_id):
            # ---- Step 1: 构建消息 ----
            messages = self._build_messages(question, history, last_context)

            # ---- Step 2: ReAct 循环 ----
            tool_calls_log = []
            chart_html = None
            sql_data = None
            last_sql_data = []   # 最近一次 sql_query 的数据，供 data_process 自动回填

            memory = CallMemory(max_retry=self.failed_call_max_retry)
            evaluator = Evaluator(
                soft_iteration_limit=self.soft_iteration_limit,
                max_iterations=self.max_iterations,
            )
            force_answer = False
            force_answer_reason: Optional[str] = None  # 首次触发强制收口的 Evaluator 原因
            audit_rounds = 0            # 阶段2：审计打回次数（上限见 CoverageAuditor.max_rounds）
            audit_status_last = "n/a"   # 最近一轮审计的逐轮状态
            audit_missing_last: List[str] = []  # 最近一次「审计发现未答」的子问题文本

            for iteration in range(1, self.max_iterations + 1):
                with self.tracer.span(f"react.iter_{iteration}") as span:
                    print(f"\n[ReAct] Iter {iteration}/{self.max_iterations}")

                    # LLM 调用（force_answer → tool_choice="none" 强制作答）
                    tool_choice = self._resolve_tool_choice(
                        iteration=iteration, force_answer=force_answer,
                        question=question, last_context=last_context)
                    try:
                        response = self.llm.chat_with_tools(
                            messages=messages,
                            tools=self.tool_registry.to_openai_schemas(),
                            tool_choice=tool_choice,
                        )
                    except Exception as e:
                        print(f"  [ReAct] LLM 调用失败: {e}")
                        self.tracer.add_event("llm_error", error=str(e)[:200])
                        return AgentResult(
                            answer=f"系统处理请求时遇到问题：{e}",
                            tool_calls_log=tool_calls_log,
                            iterations=iteration,
                            source="error",
                            session_id=session_id,
                            metrics=_build_metrics(
                                iterations=iteration, tool_calls_log=tool_calls_log,
                                memory=memory, force_answer=force_answer,
                                force_answer_reason=force_answer_reason,
                                converged_reason="llm_error", source="error"),
                        )

                    # --- 情况 A: LLM 要调工具 ---
                    if response.finish_reason == "tool_calls" or response.tool_calls:
                        if force_answer:
                            # tool_choice="none" 下仍返回工具调用（罕见）→ 不再执行，直接作答
                            answer = response.content or "抱歉，基于已收集的信息我暂时无法给出准确结论。"
                            metrics = _build_metrics(
                                iterations=iteration, tool_calls_log=tool_calls_log,
                                memory=memory, force_answer=force_answer,
                                force_answer_reason=force_answer_reason,
                                converged_reason="force_answer_tool_calls",
                                source="agent")
                            print(f"  [ReAct] 指标: {metrics}")
                            return AgentResult(
                                answer=answer,
                                tool_calls_log=tool_calls_log,
                                iterations=iteration,
                                source="agent",
                                chart_html=chart_html,
                                sql_data=sql_data,
                                session_id=session_id,
                                trace=self.tracer.to_dict(),
                                metrics=metrics,
                            )

                        # 记录 assistant 消息
                        assistant_msg = self._build_assistant_msg(response)
                        messages.append(assistant_msg)

                        round_has_data = False
                        round_all_dup = True
                        round_last_fp = None

                        for tc in response.tool_calls:
                            print(f"  [ReAct] → 调用工具: {tc.name}"
                                  f"({json.dumps(tc.arguments, ensure_ascii=False)[:80]})")

                            # ---- D2 去重拦截 ----
                            if self.call_dedup:
                                rec = memory.lookup(tc.name, tc.arguments)
                                if rec is not None and rec.ok:
                                    memory.mark_dup()
                                    messages.append({
                                        "role": "tool",
                                        "tool_call_id": tc.id,
                                        "content": (rec.result_content + "\n\n"
                                            f"[该工具与参数已执行过（第 {rec.iteration} 轮），结果同上。"
                                            "请基于已有信息作答；若需不同口径请改变参数或更换工具。]"),
                                    })
                                    tool_calls_log.append({
                                        "iteration": iteration,
                                        "tool": tc.name,
                                        "arguments": tc.arguments,
                                        "dup": True,
                                        "result_preview": rec.result_content[:200],
                                    })
                                    continue
                                if rec is not None and not rec.ok and not memory.can_retry(rec):
                                    memory.mark_dup()
                                    messages.append({
                                        "role": "tool",
                                        "tool_call_id": tc.id,
                                        "content": (f"[该工具调用此前已失败过（第 {rec.iteration} 轮），"
                                            "重试仍未成功。请改变参数或更换工具，不要再重复同样调用。]"),
                                    })
                                    tool_calls_log.append({
                                        "iteration": iteration,
                                        "tool": tc.name,
                                        "arguments": tc.arguments,
                                        "dup": True,
                                        "result_preview": "[dup-failed]",
                                    })
                                    continue

                            # ---- 正常执行 ----
                            round_all_dup = False
                            call_args = dict(tc.arguments)
                            if tc.name == "data_process":
                                # data_process 必须基于最近一次 sql_query 的数据；
                                # LLM 若把 66 行全量塞进 data 参数，既慢又易格式出错。
                                # 这里统一用引擎侧已收集的 last_sql_data 回填，避免重传大数据。
                                call_args["data"] = last_sql_data or []

                            with self.tracer.span(f"tool.{tc.name}",
                                                  args=str(call_args)[:100]):
                                result_str = await self.tool_registry.execute(
                                    tc.name, **call_args
                                )

                            # 解析结果
                            try:
                                result_data = json.loads(result_str)
                            except json.JSONDecodeError:
                                result_data = {"raw": result_str}
                            # json.loads("null") 返回 None，兜底
                            if result_data is None:
                                result_data = {"success": False, "error": "工具返回为空"}

                            # ---- D3 错误分类 + 指引 ----
                            guidance = ""
                            if isinstance(result_data, dict) and not result_data.get("success"):
                                guidance = render_guidance(classify_error(result_data))

                            injected = _tool_message_content(result_str, result_data)
                            if guidance:
                                injected = injected + "\n\n" + guidance

                            # ---- D2 记录 ----
                            rec = memory.record(
                                tc.name, tc.arguments, result_data, injected, iteration,
                            )
                            if isinstance(result_data, dict) and result_data.get("success") and result_data.get("data"):
                                round_has_data = True
                            round_last_fp = rec.result_fp

                            tool_calls_log.append({
                                "iteration": iteration,
                                "tool": tc.name,
                                "arguments": tc.arguments,
                                "result_preview": result_str[:200],
                                "result_parsed": result_data,
                            })

                            # 收集图表和 SQL 数据
                            if tc.name == "chart" and not chart_html:
                                chart_html = result_data.get("chart_html")
                            if tc.name == "sql_query":
                                last_sql_data = result_data.get("data") or []
                                if not sql_data:
                                    sql_data = last_sql_data

                            # 注入工具结果（截断预览，chart_html 不进 LLM）
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": injected,
                            })

                        # ---- 每轮末：D1 收敛判定 ----
                        if self.evaluator_enabled:
                            ev = evaluator.evaluate(question, memory, {
                                "has_data": round_has_data,
                                "all_dup": round_all_dup,
                                "last_result_fp": round_last_fp,
                            }, iteration)
                            if ev.force_answer:
                                if not force_answer:
                                    force_answer_reason = ev.reason
                                force_answer = True
                            if ev.sufficient and not ev.force_answer:
                                messages.append({
                                    "role": "user",
                                    "content": "[已收集足够信息，请基于以上数据直接作答，无需再查；如仍有遗漏请明确说明缺什么。]",
                                })

                        continue  # 下一轮迭代

                    # --- 情况 B: LLM 直接返回答案 ---
                    if response.content:
                        print(f"  [ReAct] ✓ 最终回答 ({len(response.content)} 字符)")

                        # ---- 阶段2：D10 覆盖审计（收敛前独立验收，fail-open）----
                        # 插入点=唯一正常收敛出口；force_answer 时只记录不打回
                        # （Evaluator 一票强制收口权不可被架空）。
                        auditor = getattr(self, "coverage_auditor", None)
                        if auditor is not None:
                            if not auditor.should_audit(
                                    force_answer=force_answer,
                                    audit_rounds=audit_rounds,
                                    tool_calls_log=tool_calls_log):
                                audit_status_last = "skipped"
                            else:
                                audit = auditor.audit(question, tool_calls_log,
                                                      response.content)
                                if not audit.ok:
                                    audit_status_last = "error"
                                    print("  [ReAct] 覆盖审计不可用（fail-open），"
                                          f"按原答案收敛: {audit.error}")
                                elif not audit.missing:
                                    audit_status_last = "passed"
                                elif force_answer:
                                    audit_status_last = "force_not_bounced"
                                    audit_missing_last = [m["text"] for m in audit.missing]
                                    print(f"  [ReAct] 覆盖审计发现 "
                                          f"{len(audit_missing_last)} 个未答子问题，"
                                          "但已强制收口 → 仅记录不打回")
                                else:
                                    audit_rounds += 1
                                    audit_status_last = "bounced"
                                    audit_missing_last = [m["text"] for m in audit.missing]
                                    print(f"  [ReAct] 覆盖审计未通过"
                                          f"（{len(audit_missing_last)} 个未答）"
                                          f"→ 打回第 {audit_rounds} 次")
                                    self.tracer.add_event(
                                        "coverage_audit_bounced",
                                        missing=[m["text"] for m in audit.missing])
                                    messages.append({"role": "user",
                                                     "content": audit.advice})
                                    continue

                        self.tracer.add_event("final_answer",
                                              iterations=iteration,
                                              tools_used=len(tool_calls_log))

                        converged = (f"force_answer:{force_answer_reason}"
                                     if force_answer else "final_answer")
                        metrics = _build_metrics(
                            iterations=iteration, tool_calls_log=tool_calls_log,
                            memory=memory, force_answer=force_answer,
                            force_answer_reason=force_answer_reason,
                            converged_reason=converged, source="agent",
                            audit_rounds=audit_rounds,
                            # run 级状态：只要打回过就记 bounced（不被后续轮的
                            # passed/skipped 覆盖），否则取最后一轮的逐轮状态。
                            audit_status=("bounced" if audit_rounds > 0
                                          else audit_status_last),
                            audit_missing_count=len(audit_missing_last),
                            audit_missing=audit_missing_last[:8])
                        print(f"  [ReAct] 指标: {metrics}")
                        return AgentResult(
                            answer=response.content,
                            tool_calls_log=tool_calls_log,
                            iterations=iteration,
                            source="agent",
                            chart_html=chart_html,
                            sql_data=sql_data,
                            session_id=session_id,
                            trace=self.tracer.to_dict(),
                            metrics=metrics,
                        )

                    # --- 情况 C: 空响应 ---
                    print(f"  [ReAct] ⚠ 空响应")
                    return AgentResult(
                        answer="抱歉，我暂时无法处理这个问题。请尝试换个问法。",
                        tool_calls_log=tool_calls_log,
                        iterations=iteration,
                        source="error",
                        session_id=session_id,
                        metrics=_build_metrics(
                            iterations=iteration, tool_calls_log=tool_calls_log,
                            memory=memory, force_answer=force_answer,
                            force_answer_reason=force_answer_reason,
                            converged_reason="empty_response", source="error"),
                    )

            # ---- 超出最大迭代 ----
            print(f"  [ReAct] ⚠ 达到最大迭代次数 ({self.max_iterations})")
            metrics = _build_metrics(
                iterations=self.max_iterations, tool_calls_log=tool_calls_log,
                memory=memory, force_answer=force_answer,
                force_answer_reason=force_answer_reason,
                converged_reason="max_iterations", source="max_iterations")
            print(f"  [ReAct] 指标: {metrics}")
            return AgentResult(
                answer="抱歉，处理您的问题时步骤较多，请尝试更具体的提问方式。",
                tool_calls_log=tool_calls_log,
                iterations=self.max_iterations,
                source="max_iterations",
                session_id=session_id,
                metrics=metrics,
            )

    # ================================================================
    # 消息构建
    # ================================================================

    # ---- P2：追问/校验类问题强制重查路由 ----
    _VERIFY_PATTERNS = re.compile(
        r"你确定|确定吗|对吗|对不对|是否正确|是不是|靠谱吗|准吗|"
        r"确认一下|再核实|复核|核对|名单(对|正确|靠谱)"
    )

    @classmethod
    def _is_verification_question(cls, question: str) -> bool:
        """判断是否为校验/追问类问题（需要重查数据，而不是凭记忆作答）。"""
        q = str(question or "").strip()
        if not q:
            return False
        return len(q) <= 24 and bool(cls._VERIFY_PATTERNS.search(q))

    def _resolve_tool_choice(self, *, iteration: int, force_answer: bool,
                             question: str, last_context: Dict) -> str:
        """P2：校验类追问首轮强制调用工具重查，不许直接吐“无法确认”模板。"""
        if force_answer:
            return "none"
        if (iteration == 1 and last_context
                and self._is_verification_question(question)):
            return "required"
        return "auto"

    def _build_messages(self, question: str, history: List[Dict],
                        last_context: Dict) -> List[Dict]:
        """
        构建 LLM 消息列表

        核心改进（替代 FollowupResolver）:
          不依赖关键词匹配，而是通过上下文注入让 LLM 语义理解追问。

          注入格式:
            [上下文提示：上一轮查询了白云山2024年净利润45.2亿元。
             当前问题可能是追问，请自动继承公司/年份/指标信息。]
        """
        with self.tracer.span("build_messages"):
            # 1. 系统 Prompt
            tool_descriptions = self.tool_registry.get_tool_descriptions()
            extra_context = ""

            # 2. 上下文注入（从 last_context 构建）
            if last_context:
                extra_context = self.prompt_manager.build_context_hint(
                    last_company=last_context.get("company", ""),
                    last_indicator=last_context.get("indicator", ""),
                    last_year=last_context.get("year", ""),
                    last_answer_summary=last_context.get("answer_summary", ""),
                )

            system_prompt = self.prompt_manager.build_system_prompt(
                tool_descriptions=tool_descriptions,
                extra_context=extra_context,
            )

            messages = [{"role": "system", "content": system_prompt}]

            # 3. 对话历史（最近 N 轮）
            if history:
                recent = history[-(self.history_turns * 2):]
                for msg in recent:
                    content = str(msg.get("content", ""))[:500]
                    messages.append({
                        "role": msg.get("role", "user"),
                        "content": content,
                    })

            # 4. 当前用户问题
            #    P1：上下文只由 system prompt 的「当前会话上下文」承载一次，
            #        不再在 user 消息里重复注入，避免追问被“继承上一轮”带偏。
            user_content = question
            #    P5：校验类追问（“你确定吗/对吗”）必须重跑上一轮原查询，
            #         否则当前短问句抽不到实体 → 无命中 → 模型误判“数据不存在”。
            if self._is_verification_question(question):
                prev_q = ""
                for msg in reversed(history or []):
                    if msg.get("role") == "user":
                        prev_q = str(msg.get("content", ""))
                        break
                if prev_q:
                    user_content = (
                        question + "\n\n"
                        "[请重新执行上一轮查询以核对；若重查返回空（无命中），"
                        "先说明“该口径在库中无记录/与上一轮查询口径不一致”，"
                        "不要据此判定上一轮答案是编造的。上一轮问题如下："
                        + prev_q + "]"
                    )
            messages.append({"role": "user", "content": user_content})

            print(f"  [ReAct] 消息构建: system + {len(messages)-1} 条，"
                  f"{'有' if last_context else '无'}上下文")
            return messages

    def _build_assistant_msg(self, response: LLMResponse) -> Dict:
        """将 LLMResponse 转为 OpenAI 格式的 assistant 消息"""
        tool_calls_list = []
        for tc in response.tool_calls:
            tool_calls_list.append({
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                },
            })

        msg = {
            "role": "assistant",
            "content": response.content,
            "tool_calls": tool_calls_list,
        }

        # DeepSeek reasoning_content
        if response.reasoning_content:
            msg["reasoning_content"] = response.reasoning_content

        return msg


# ============================================================
# 全局单例
# ============================================================

_agent_instance: Optional[ReActAgent] = None


def get_react_agent() -> ReActAgent:
    """获取全局 ReActAgent 单例"""
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = ReActAgent()
    return _agent_instance
