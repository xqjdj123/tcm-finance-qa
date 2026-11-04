# -*- coding: utf-8 -*-
"""工具基类 — RQagentPro 风格: async execute + JSON Schema + 优先级"""
import json
from abc import ABC, abstractmethod
from typing import Dict, Any

# ==================== 报告期语义（全工具单一来源）====================
# 背景：财务库同一公司同一年存在 FY/Q3/H1/Q1 四行，必须按“报告期覆盖长度”
# 优先选取，否则会出现两类缺陷（见 doc/评测结果分析-20260913.md）：
#   N1 排名类不加报告期约束 -> 同一公司多行挤占 top_k 名额
#   N2 用 report_period DESC 字符串排序 -> 'Q3' > 'Q1' > 'H1' > 'FY'，取到 Q3
# 覆盖度：FY(全年) > Q3(前三季累计，9 个月) > H1(半年，6 个月) > Q1(一季度，3 个月)
PERIOD_PRIORITY = {"FY": 4, "Q3": 3, "H1": 2, "Q1": 1}
PERIOD_ORDER = ["FY", "Q3", "H1", "Q1"]


def period_priority(period) -> int:
    """报告期优先级权重（越大越优先）；未知/空返回 0。"""
    return PERIOD_PRIORITY.get((period or "").strip().upper(), 0)


def pick_best_period(periods) -> str:
    """从若干报告期中选出覆盖度最高的一个；全空返回 ''。"""
    best = ""
    for p in periods or []:
        if period_priority(p) > period_priority(best):
            best = (p or "").strip().upper()
    return best


def period_priority_sql(col: str = "report_period") -> str:
    """生成 MySQL 端“按覆盖度优先”的排序表达式（FIELD 返回 1 起下标，越小越优先）。

    用法：ORDER BY report_year DESC, {expr} ASC
    """
    items = ",".join("'%s'" % p for p in PERIOD_ORDER)
    return "FIELD(%s,%s)" % (col, items)


class BaseTool(ABC):
    """
    Agent 工具基类

    每个工具必须定义:
      - name: 工具名（LLM 调用时使用）
      - description: 自然语言描述（LLM 理解工具用途）
      - parameters: OpenAI Function Calling 兼容的 JSON Schema
      - priority: 优先级（越高越靠前，也影响 tool choice 偏好）

    必须实现:
      - async execute(**kwargs) -> str: 异步执行，返回 JSON 字符串

    保留兼容:
      - run(inputs: dict) -> dict: 同步包装器（旧代码兼容）
    """

    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }
    priority: int = 50  # 默认优先级

    @abstractmethod
    async def execute(self, **kwargs) -> str:
        """
        异步执行工具，返回 JSON 字符串

        参数由 LLM 根据 parameters schema 传入（keyword arguments）
        返回必须是有效的 JSON 字符串，包含至少 success 字段
        """
        ...

    def normalize_inputs(self, inputs: dict, understanding: dict) -> dict:
        """把补全/归一结果合并进本工具各分支所需的入参；只填空缺/空值键。

        默认空转（向后兼容，不强制所有工具实现）；子类如 SQLTool 可覆盖，
        复用其内部既有的补全逻辑（如 _merge_understanding）。
        """
        return inputs

    def run(self, inputs: dict) -> dict:
        """
        同步包装器 — 兼容旧版 FinanceAgent._execute_plan()

        默认实现：将 inputs dict 转为 kwargs 调用 execute()，
        然后解析 JSON 返回 dict。

        子类可以覆盖此方法提供更高效的同步实现。
        """
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 已有事件循环在运行，创建新的
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run, self.execute(**inputs)
                    )
                    result_json = future.result(timeout=30)
            else:
                result_json = asyncio.run(self.execute(**inputs))
        except RuntimeError:
            result_json = asyncio.run(self.execute(**inputs))

        try:
            return json.loads(result_json)
        except json.JSONDecodeError:
            return {"success": False, "error": "工具返回了无效JSON", "raw": result_json}

    def to_openai_schema(self) -> dict:
        """生成 OpenAI Function Calling 兼容的工具 Schema"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description.strip(),
                "parameters": self.parameters,
            }
        }

    def to_schema(self) -> dict:
        """返回工具的简要信息（旧版兼容）"""
        return {
            "name": self.name,
            "description": self.description,
        }

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name={self.name}, priority={self.priority})>"
