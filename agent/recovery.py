# -*- coding: utf-8 -*-
"""错误分类与恢复指引（D3）。

一期覆盖三类确定性失败：no_data / field_not_found / missing_param。
前两类决定"失败调用能否被硬拦截"——重试无意义，必须尽早把"别再查了 /
换指标"的指令发给模型，否则失败重试例外会退化成新一轮死循环；
missing_param（缺参数）属确定性失败，指引模型补齐参数或改用
query_type=auto，避免白烧迭代轮次。timeout / db_error / unknown 挂二期。
"""

ERROR_KINDS = ("missing_param", "field_not_found", "no_data",
               "timeout", "db_error", "unknown")

_MISSING_HINTS = ("缺少", "缺失", "参数", "company", "companies")
_FIELD_HINTS = ("字段", "指标不存在", "指标未", "找不到", "不存在", "field", "未识别")
_NO_DATA_HINTS = ("无数据", "未查询到", "没有数据", "0 条", "所有重试均无数据",
                  "暂无数据", "未找到数据")


def classify_error(result: dict) -> str:
    """成功返回空串；否则按 error/reason/message 归入 ERROR_KINDS 之一。"""
    if not isinstance(result, dict):
        return "unknown"
    if result.get("success"):
        return ""

    blob = " ".join(str(result.get(k) or "") for k in ("error", "reason", "message"))
    data = result.get("data")

    # 明确查了但没数据（data 为明确空列表）
    if isinstance(data, list) and len(data) == 0:
        if any(k in blob for k in _MISSING_HINTS):
            return "missing_param"
        if any(k in blob for k in _FIELD_HINTS):
            return "field_not_found"
        return "no_data"

    # data 非空或 None
    if any(k in blob for k in _MISSING_HINTS):
        return "missing_param"
    if any(k in blob for k in _FIELD_HINTS):
        return "field_not_found"
    if any(k in blob for k in _NO_DATA_HINTS):
        return "no_data"
    return "unknown"


def render_guidance(kind: str, context: dict = None) -> str:
    """按错误类型生成注入给模型的下一步指引；未知/二期类型返回空串。"""
    context = context or {}
    if kind == "no_data":
        return ("【查询无数据】该条件下数据库没有对应记录，请勿再以同样条件重查；"
                "直接向用户说明库中暂无该数据，并指出可能缺失的口径（年份/期间/指标）。")
    if kind == "field_not_found":
        return ("【字段不存在】未找到该指标，请换用以下可用字段之一："
                + _field_list(context.get("limit", 8))
                + "。不要再次查询不存在的指标。")
    if kind == "missing_param":
        return ("【参数缺失】本次查询缺少必要参数（如 company/companies、"
                "metric/indicators、year/years）。请补齐缺失参数，或改用 "
                "query_type=auto 由工具自动解析；不要以相同缺参方式重复调用。")
    # timeout / db_error / unknown：一期不注入指引
    return ""


def _field_list(limit: int = 8) -> str:
    try:
        from field_dict import SCHEMA_DICT
        labels = [info["label"] for info in list(SCHEMA_DICT.values())[:limit]]
        return "、".join(labels)
    except ImportError:
        return "（字段字典暂不可用）"
