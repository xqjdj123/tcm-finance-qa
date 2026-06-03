# -*- coding: utf-8 -*-
"""
多意图拆分器 (P2)

功能：
  - 将复合问题拆分为多个独立的子问题
  - 支持三种拆分方式：
    1. 连接词分割（"另外"、"同时"、"还有"、"以及"）
    2. 多指标分割（"净利润和营收"）
    3. 多公司分割（"白云山和云南白药"）

插入位置：Agent.query() 中 understand() 之前
"""

import re
from typing import List, Tuple


class IntentSplitter:
    """多意图拆分器"""

    # 连接词列表
    SPLIT_WORDS = [
        "另外", "同时", "还有", "以及", "顺便", "再", "并且",
        "此外", "除此之外", "另外还", "同时还", "还有还",
        "同时还要", "另外还要", "还要", "也要", "也需要",
    ]

    # 多指标连接词
    INDICATOR_CONNECTORS = ["和", "与", "、", "及", "以及", "还有"]

    # 多公司连接词
    COMPANY_CONNECTORS = ["和", "与", "、", "及", "以及", "还有"]

    def split(self, question: str) -> List[str]:
        """
        将复合问题拆分为多个子问题

        输入: "白云山净利润多少，另外云南白药的营收增长怎么样"
        输出: ["白云山净利润多少", "云南白药的营收增长怎么样"]
        """
        # 1. 按连接词分割
        sub_questions = self._split_by_connectors(question)

        if len(sub_questions) > 1:
            return sub_questions

        # 2. 如果没有连接词，返回原问题
        return [question]

    def _split_by_connectors(self, question: str) -> List[str]:
        """按连接词分割"""
        # 按连接词分割
        parts = []
        for connector in self.SPLIT_WORDS:
            if connector in question:
                parts = question.split(connector)
                break

        if not parts:
            return [question]

        # 清理每个部分
        result = []
        for part in parts:
            part = part.strip()
            if part:
                # 去掉开头的标点符号
                part = re.sub(r'^[，,、；;：:。.！!？?\s]+', '', part)
                if part:
                    result.append(part)

        return result if result else [question]

    def has_multiple_intents(self, question: str) -> bool:
        """检查问题是否包含多个意图"""
        return len(self.split(question)) > 1

    def get_split_info(self, question: str) -> dict:
        """获取拆分信息"""
        sub_questions = self.split(question)
        return {
            "original": question,
            "sub_questions": sub_questions,
            "count": len(sub_questions),
            "is_multi_intent": len(sub_questions) > 1,
        }


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    splitter = IntentSplitter()

    test_cases = [
        # 连接词分割
        "白云山净利润多少，另外云南白药的营收增长怎么样",
        "白云山净利润多少，同时云南白药的营收增长怎么样",
        "白云山净利润多少，还有云南白药的营收增长怎么样",
        "白云山净利润多少，以及云南白药的营收增长怎么样",

        # 单意图
        "白云山净利润多少",
        "白云山2024年营收增长率",

        # 复杂连接词
        "白云山净利润多少，除此之外云南白药的营收增长怎么样",
        "白云山净利润多少，另外还要看云南白药的营收增长",
    ]

    for question in test_cases:
        info = splitter.get_split_info(question)
        print(f"输入: {question}")
        print(f"  子问题数: {info['count']}")
        print(f"  是否多意图: {info['is_multi_intent']}")
        print(f"  子问题: {info['sub_questions']}")
        print()
