# -*- coding: utf-8 -*-
"""收敛判据（D1）—— 规则前置（零成本、确定性），LLM 兜底留二期。

规则层负责"够了"（一票强制收口权，不漂）；"缺什么"需要语义，交给二期 LLM。
引擎不解析自然语言：充分性只读工具返回体（success/data），槽位只读 CallMemory。
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Evaluation:
    sufficient: bool
    force_answer: bool          # True → 下一轮 tool_choice="none" 强制作答
    reason: str                 # no_new_info | enough_data | soft_limit | need_more
    missing: List[str] = field(default_factory=list)


class Evaluator:
    def __init__(self, soft_iteration_limit: int = 5, max_iterations: int = 8):
        self.soft_limit = soft_iteration_limit
        self.max_iterations = max_iterations
        self._prev_round_fp: Optional[str] = None

    def evaluate(self, question: str, memory, round_info: Dict[str, Any],
                 iteration: int) -> Evaluation:
        """
        round_info 由引擎每轮末汇总：
          has_data:       本轮是否拿到 success=true 且 data 非空
          all_dup:        本轮全部工具调用是否被 CallMemory 拦截
          last_result_fp: 本轮最后一次工具调用的 result_fp
        """
        has_data = bool(round_info.get("has_data"))
        all_dup = bool(round_info.get("all_dup"))
        last_fp = round_info.get("last_result_fp")

        # 1. no_new_info：本轮全部被拦截，或 result_fp 与上一轮相同（打转）
        if all_dup or (last_fp is not None and last_fp == self._prev_round_fp):
            self._prev_round_fp = last_fp
            return Evaluation(True, True, "no_new_info")

        self._prev_round_fp = last_fp

        # 2. soft_limit：到软上限且已有可用数据 → 强制收口
        if iteration >= self.soft_limit and (has_data or (memory is not None and memory.has_success())):
            return Evaluation(True, True, "soft_limit")

        # 3. enough_data：有数据 → 软提示，不强制（多公司/多指标可能只查回一部分）
        if has_data:
            return Evaluation(True, False, "enough_data")

        # 4. 数据不足，继续（hard_limit 由引擎 for 循环兜底）
        return Evaluation(False, False, "need_more")
