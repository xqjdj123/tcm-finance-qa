# -*- coding: utf-8 -*-
"""查询槽位的自然语言归一 —— 中文数字与「前 N」解析的**唯一事实源**

## 收敛背景（2026-09-14）
同一件事曾有三份实现，且互不同步：

| 位置 | 形态 | 问题 |
|---|---|---|
| `models/.../time_parser.py::CN_NUM` | dict，含「两」 | 最全 |
| `models/.../pipeline.py::_cn_num` | 方法内局部 dict | 缺「两」 |
| `agent/tools/sql_tool.py::cn_num` | 方法内局部 dict | 缺「两」 |
| 正则「前 + 数字/中文数词」 | pipeline + sql_tool 各写一遍 | 漂移 |

本模块只做**纯字符串归一**，不依赖模型、不依赖 DB、无第三方库。

> 说明：这里的"前 N / 第 N"属**领域槽位**解析（排名取前几名），
> 与 `pdf_extractor` 里识别"一、二、"章节标题的用法**不是同一件事**，
> 故那处（`field_mapper.py` / `pdf_segmenter.py`）未被本模块收编。
"""
import re

# 中文数词 → 数字。含「两」，与 time_parser 历史取值范围一致。
CN_DIGITS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

# 数词串（阿拉伯数字 or 中文数词），供拼接进更复杂的模式
_NUM = r"(\d+|[一二两三四五六七八九十]+)"

# 「前 5」「前五」「前 10 名」
TOPK_RE = re.compile(r"前\s*" + _NUM)

# 「第 5」「第五名」
ORDINAL_RE = re.compile(r"第\s*" + _NUM)

# 排名/交集类问题的信号词（"均/都/同时 ... 排名 ... 前 N" = 多指标交集榜单）
INTERSECTION_RE = re.compile(r"(?:均|都|同时).*?排名.*?前\s*" + _NUM)


def _to_int(raw: str):
    """数词串 → int；无法识别返回 None（不猜、不默认）。"""
    if raw is None:
        return None
    if raw.isdigit():
        return int(raw)
    return CN_DIGITS.get(raw)


def parse_top_k(text):
    """从文本解析「前 N」的 N；无则返回 None。

    例：`2024年营收前五` → 5；`前 10 名` → 10；`营收是多少` → None。
    """
    if not text or not isinstance(text, str):
        return None
    m = TOPK_RE.search(text)
    return _to_int(m.group(1)) if m else None


def parse_ordinal(text):
    """从文本解析「第 N」的 N；无则返回 None。"""
    if not text or not isinstance(text, str):
        return None
    m = ORDINAL_RE.search(text)
    return _to_int(m.group(1)) if m else None


def parse_intersection_topk(text):
    """解析「均/都/同时 … 排名 … 前 N」这类**多指标交集榜单**问题的 N。

    这是"多意图"里唯一需要结构化解析的一种（同一批公司要在多个指标上都进前 N），
    判定完全基于连接词+排名结构，与具体公司名/指标名无关。
    非交集句式返回 None。
    """
    if not text or not isinstance(text, str):
        return None
    m = INTERSECTION_RE.search(text)
    return _to_int(m.group(1)) if m else None


if __name__ == "__main__":
    cases = ["2024年营收前五的中药企业", "前 10 名", "排名前三", "营收是多少",
             "找出前五名，且净利润也排名前十", "第一梯队"]
    for c in cases:
        print(f"{c}  ->  top_k={parse_top_k(c)}  ordinal={parse_ordinal(c)} "
              f"intersection={parse_intersection_topk(c)}")
