# -*- coding: utf-8 -*-
"""
结果合并器 (P2)

功能：
  - 将多个子问题的答案合并为一个连贯的回复
  - 支持多种合并方式：
    1. 简单拼接
    2. 按主题分组
    3. 生成总结

插入位置：Agent.query() 中 _summarize() 之后
"""

from typing import List, Dict, Any


class ResultMerger:
    """结果合并器"""

    def merge(self, results: List[Dict[str, Any]], original_query: str) -> Dict[str, Any]:
        """
        合并多个子问题的结果

        输入: [
            {"question": "白云山净利润", "answer": "白云山2024年净利润为100亿元", ...},
            {"question": "云南白药营收增长", "answer": "云南白药2024年营收增长15%", ...},
        ]
        输出: {"answer": "1. 白云山净利润：...\n2. 云南白药营收增长：...", ...}
        """
        if not results:
            return {"answer": "未查询到相关数据"}

        if len(results) == 1:
            return results[0]

        # 合并答案
        merged_answer = self._merge_answers(results)

        # 合并其他字段
        merged_result = {
            "answer": merged_answer,
            "task_type": "multi_intent",
            "plan": self._merge_plans(results),
            "steps": sum(r.get("steps", 0) for r in results),
            "history": self._merge_history(results),
            "session_id": results[0].get("session_id", "default"),
            "confidence": min(r.get("confidence", 0.85) for r in results),
            "chart": self._merge_charts(results),
            "sub_results": results,
        }

        return merged_result

    def _merge_answers(self, results: List[Dict[str, Any]]) -> str:
        """合并答案"""
        answers = []
        for i, result in enumerate(results, 1):
            question = result.get("question", "")
            answer = result.get("answer", "")

            if question and answer:
                # 提取公司名和指标
                company = self._extract_company(question)
                indicator = self._extract_indicator(question)

                if company and indicator:
                    answers.append(f"{i}. {company}的{indicator}：{answer}")
                elif company:
                    answers.append(f"{i}. {company}：{answer}")
                else:
                    answers.append(f"{i}. {answer}")
            elif answer:
                answers.append(f"{i}. {answer}")

        return "\n".join(answers)

    def _extract_company(self, question: str) -> str:
        """从问题中提取公司名"""
        companies = [
            "白云山", "云南白药", "华润三九", "同仁堂", "太极集团", "片仔癀",
            "东阿阿胶", "江中药业", "健民集团", "千金药业", "羚锐制药",
            "济川药业", "瑞康医药", "昆药集团", "康美药业", "康缘药业",
            "康弘药业", "振东制药", "桂林三金", "太龙药业", "康恩贝",
            "天士力", "达仁堂", "葵花药业", "金花股份", "金花",
        ]
        for company in companies:
            if company in question:
                return company
        return ""

    def _extract_indicator(self, question: str) -> str:
        """从问题中提取指标"""
        indicators = [
            "净利润", "营收", "营业收入", "营业总收入", "利润总额",
            "毛利率", "净利率", "ROE", "ROA", "资产负债率",
            "增长率", "营收增长", "净利润增长", "同比增长",
        ]
        for indicator in indicators:
            if indicator in question:
                return indicator
        return ""

    def _merge_plans(self, results: List[Dict[str, Any]]) -> List[str]:
        """合并执行计划"""
        plans = []
        for result in results:
            plan = result.get("plan", [])
            if isinstance(plan, list):
                plans.extend(plan)
            elif isinstance(plan, str):
                plans.append(plan)
        return list(set(plans))  # 去重

    def _merge_history(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """合并执行历史"""
        history = []
        for result in results:
            result_history = result.get("history", [])
            if isinstance(result_history, list):
                history.extend(result_history)
        return history

    def _merge_charts(self, results: List[Dict[str, Any]]) -> str:
        """合并图表"""
        charts = []
        for result in results:
            chart = result.get("chart")
            if chart:
                charts.append(chart)
        return charts[0] if charts else None


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    merger = ResultMerger()

    test_results = [
        {
            "question": "白云山净利润",
            "answer": "白云山2024年净利润为100亿元",
            "task_type": "query",
            "plan": ["sql_query"],
            "steps": 1,
            "history": [],
            "session_id": "test",
            "confidence": 0.85,
        },
        {
            "question": "云南白药营收增长",
            "answer": "云南白药2024年营收增长15%",
            "task_type": "query",
            "plan": ["sql_query"],
            "steps": 1,
            "history": [],
            "session_id": "test",
            "confidence": 0.85,
        },
    ]

    result = merger.merge(test_results, "白云山净利润多少，另外云南白药的营收增长怎么样")
    print(f"合并结果:")
    print(f"  答案: {result['answer']}")
    print(f"  任务类型: {result['task_type']}")
    print(f"  执行计划: {result['plan']}")
    print(f"  子结果数: {len(result['sub_results'])}")
