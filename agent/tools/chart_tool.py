# -*- coding: utf-8 -*-
"""图表生成工具：支持智能选图

数据适配层（重要 — 2026-09-14 返修）：
LLM 传入的 ``data`` 形状并不固定，至少三种常见形态：
  ① [{"year": "2022", "营业总收入": 364.88}, ...]          # 中文列名 + year
  ② [{"stock_abbr": "云南白药", "report_year": 2024,
      "营业总收入": 400.33}, ...]                          # 数据库列名
  ③ {"labels": [...], "values": [...]}                     # 直接给轴

本工具**不假设键名**，而是按"值特征"自动识别 —— 年份列 / 公司列 / 数值列，
再据此选图型、定轴标签、从标题推断单位。

修复前的缺陷（已在 2026-09-14 复现）：
  · 数值列取 ``val_keys[0]``（字典第一键），而排除名单只有数据库列名 →
    ``{"year":"2022","营业总收入":364.88}`` 会选中 ``year``，
    把 **2022/2023/2024 当作数值**画上去；
  · X 轴写死 ``r.get("report_year")`` → 键不存在 → 轴标签全部退化成 ``?``；
  · ``ylabel="万元"`` 写死 → 与标题里的"亿元"自相矛盾。
"""

import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from agent.tools.base import BaseTool


# ---- 列名线索（用于识别，不作为硬性要求）----
_YEAR_KEYS = {"year", "report_year", "年份", "年度", "报告年度", "报告年份"}
_COMPANY_KEYS = {"stock_abbr", "company", "company_name", "stock_name", "name",
                 "公司", "公司名称", "企业", "企业名称", "简称", "证券简称", "名称"}
_IGNORE_KEYS = {"rank", "排名", "stock_code", "证券代码", "code", "id", "序号",
                "note", "备注", "unit", "单位", "report_period", "报告期"}
# 注：'占比'/'percent'/'pct' **不可**列入黑名单 —— 占比列本身就是饼图的合法数值源。
# （2026-09-14 回归时踩过：误排除后「业务结构占比」直接判成"无数值列"。）
_METRIC_HINTS = ("营收", "收入", "净利", "利润", "资产", "负债", "现金流", "金额",
                 "总额", "毛利", "费用", "数值", "值",
                 "value", "amount", "total", "revenue", "profit", "income", "assets")
_UNIT_TOKENS = ("亿元", "万元", "个百分点", "倍", "亿", "元", "%", "％")


def _to_number(v):
    """尽量把值转成 float；转不了返回 None（兼容 '1,234.5'、'364.88亿元'、'7.19%'）"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("，", "")
    if not s:
        return None
    for u in ("亿元", "万元", "个百分点", "倍", "亿", "万", "元", "%", "％"):
        s = s.replace(u, "")
    s = s.strip()
    try:
        return float(s)
    except ValueError:
        return None


class ChartTool(BaseTool):
    name = "chart"
    description = """生成数据可视化图表。支持柱状图(bar)、折线图(line)、饼图(pie)、横向柱状图(hbar)、自动选择(auto)。
data：要可视化的数据数组，元素为对象（如 [{"年份":"2022","营业总收入":364.88}, ...]）；
工具会自动识别其中的「年份列 / 公司列 / 数值列」，无需指定键名。
返回 base64 编码的 PNG 图片 HTML。"""
    parameters = {
        "type": "object",
        "properties": {
            "chart_type": {"type": "string", "enum": ["auto", "bar", "line", "pie", "hbar"], "description": "图表类型，默认auto"},
            "data": {"type": "array", "description": "要可视化的数据数组，元素为对象；列名任意，工具自动识别年份/公司/数值列"},
            "title": {"type": "string", "description": "图表标题（若含单位如「亿元」，会作为纵轴单位）"}
        },
        "required": ["data"]
    }
    priority = 70

    async def execute(self, chart_type: str = "auto", data: list = None, title: str = "图表") -> str:
        """异步执行：与 run() 对齐，返回完整结果 JSON（含 chart_html）"""
        import json as _json
        result = self.run({"chart_type": chart_type or "auto", "data": data or [], "title": title})
        return _json.dumps(result, ensure_ascii=False)

    # ---------- 数据适配层 ----------
    @staticmethod
    def _normalize(raw):
        """把各种入参形态统一成 [ {col: val, ...}, ... ]"""
        if not raw:
            return []
        if isinstance(raw, dict):
            labs = raw.get("labels") or raw.get("x") or raw.get("categories") or raw.get("年份")
            vals = raw.get("values") or raw.get("y") or raw.get("数据") or raw.get("value")
            if isinstance(labs, list) and isinstance(vals, list):
                return [{"label": (labs[i] if i < len(labs) else ""), "value": vals[i]}
                        for i in range(len(vals))]
            return []
        if isinstance(raw, list):
            if all(isinstance(x, dict) for x in raw):
                return [x for x in raw if x]
            return [{"value": x} for x in raw if x is not None]
        return []

    @staticmethod
    def _is_year_like(key, col):
        """键名像年份，或整列取值都落在 1900–2100 的整数（且非小数值）"""
        if str(key).strip().lower() in _YEAR_KEYS:
            return True
        nums = [_to_number(v) for v in col]
        if not nums or any(n is None for n in nums):
            return False
        return all(1900 <= n <= 2100 and float(n).is_integer() for n in nums)

    def _pick_columns(self, data):
        """返回 (年份键, 公司键, 数值候选键列表, 列字典)"""
        keys = list(data[0].keys())
        cols = {k: [r.get(k) for r in data] for k in keys}

        year_key = company_key = None
        for k in keys:
            kl = str(k).strip().lower()
            if year_key is None and self._is_year_like(k, cols[k]):
                year_key = k
            elif company_key is None and kl in _COMPANY_KEYS:
                company_key = k

        candidates = []
        for k in keys:
            if k in (year_key, company_key):
                continue
            if str(k).strip().lower() in _IGNORE_KEYS:
                continue
            nums = [_to_number(v) for v in cols[k]]
            if nums and all(n is not None for n in nums):
                candidates.append(k)

        return year_key, company_key, candidates, cols

    @staticmethod
    def _pick_value_key(candidates):
        """数值列不止一个时，优先名字像业务指标的"""
        for k in candidates:
            kl = str(k).lower()
            if any(h in kl for h in _METRIC_HINTS):
                return k
        return candidates[0]

    @staticmethod
    def _infer_unit(title):
        """从标题里推断纵轴单位（长 token 优先，避免 '亿' 误吞 '亿元'）"""
        t = str(title or "")
        for u in ("亿元", "万元", "个百分点", "倍", "亿", "元", "%", "％"):
            if u in t:
                return u
        return ""

    # ---------- 主流程 ----------
    def run(self, inputs: dict) -> dict:
        chart_type = (inputs.get("chart_type") or "auto").strip()
        data = self._normalize(inputs.get("data", []))
        title = inputs.get("title", "图表") or "图表"

        if not data:
            return {"success": False, "error": "缺少data参数"}

        try:
            from chart_module import bar_chart, line_chart, hbar_chart, pie_chart

            year_key, company_key, candidates, _cols = self._pick_columns(data)
            if not candidates:
                return {"success": False, "error": "未找到数值列：data 需含可解析为数字的字段"}

            value_key = self._pick_value_key(candidates)
            rows = data[:10]
            values = [_to_number(r.get(value_key)) or 0.0 for r in rows]

            year_labels = ([str(r.get(year_key)) for r in rows] if year_key
                           else [str(i + 1) for i in range(len(rows))])
            comp_labels = ([str(r.get(company_key)) for r in rows] if company_key
                           else year_labels)

            unit = self._infer_unit(title)

            if chart_type == "auto":
                chart_type = self._detect_type(data, year_key, company_key, values)

            if chart_type == "line":
                chart_html = line_chart(year_labels, values, title=title, ylabel=unit)
            elif chart_type == "pie":
                chart_html = pie_chart(comp_labels, values, title=title)
            elif chart_type == "hbar":
                chart_html = hbar_chart(comp_labels, values, title=title, xlabel=unit)
            else:  # bar
                chart_html = bar_chart(comp_labels, values, title=title, ylabel=unit)

            return {
                "success": bool(chart_html),
                "chart_type": chart_type,
                "chart_html": chart_html,
                "title": title,
                "value_field": value_key,
                "year_field": year_key,
                "company_field": company_key,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _detect_type(self, data, year_key, company_key, values):
        """根据数据特征智能选择图表类型"""
        # 1. 时间序列数据（同一公司多年）
        years = set(str(r.get(year_key)) for r in data) if year_key else set()
        companies = set(str(r.get(company_key)) for r in data) if company_key else set()
        if len(years) >= 3 and len(companies) <= 2:
            return "line"

        # 2. 占比数据（百分比）
        total = sum(abs(v) for v in values)
        if total > 0:
            all_pct = all(0 <= abs(v) <= 1 for v in values if v != 0)
            if all_pct and abs(sum(values) - 1.0) < 0.1:
                return "pie"
            if 95 <= total <= 105 and all(0 <= v <= 100 for v in values):
                return "pie"

        # 3. 长标签
        labels = ([str(r.get(company_key)) for r in data] if company_key else [])
        max_label_len = max((len(l) for l in labels), default=0)
        if max_label_len > 8:
            return "hbar"

        # 4. 默认柱状图
        return "bar"
