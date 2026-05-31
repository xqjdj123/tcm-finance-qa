# -*- coding: utf-8 -*-
"""图表生成工具：支持智能选图"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from agent.tools.base import BaseTool


class ChartTool(BaseTool):
    name = "chart"
    description = """
    生成数据可视化图表。
    用于：趋势图、对比柱状图、占比饼图、横向柱状图
    输入：chart_type(line/bar/hbar/pie/auto), data(数据), title(标题)
    返回：图表HTML（base64编码）
    auto模式会根据数据特征自动选择图表类型
    """

    def run(self, inputs: dict) -> dict:
        chart_type = inputs.get("chart_type", "auto")
        data = inputs.get("data", [])
        title = inputs.get("title", "图表")

        if not data:
            return {"success": False, "error": "缺少data参数"}

        try:
            from chart_module import bar_chart, line_chart, hbar_chart, pie_chart

            # 获取数值字段
            val_keys = [k for k in data[0].keys()
                       if k not in ('stock_abbr', 'report_year', 'report_period', 'stock_code', 'rank', 'note')]
            if not val_keys:
                return {"success": False, "error": "无数值字段"}
            y_field = val_keys[0]

            labels = [str(r.get("stock_abbr", r.get("report_year", "?"))) for r in data[:10]]
            values = [float(r.get(y_field, 0) or 0) for r in data[:10]]

            # 智能选图
            if chart_type == "auto":
                chart_type = self._detect_type(data, labels, values)

            # 渲染图表
            if chart_type == "line":
                x_labels = [str(r.get("report_year", "?")) for r in data[:10]]
                chart_html = line_chart(x_labels, values, title=title, ylabel="万元")
            elif chart_type == "pie":
                chart_html = pie_chart(labels, values, title=title)
            elif chart_type == "hbar":
                chart_html = hbar_chart(labels, values, title=title, xlabel="万元")
            else:  # bar
                chart_html = bar_chart(labels, values, title=title, ylabel="万元")

            return {
                "success": bool(chart_html),
                "chart_type": chart_type,
                "chart_html": chart_html,
                "title": title,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _detect_type(self, data, labels, values):
        """根据数据特征智能选择图表类型"""
        # 1. 时间序列数据（同一公司多年）
        years = set(r.get("report_year") for r in data if r.get("report_year"))
        companies = set(r.get("stock_abbr") for r in data if r.get("stock_abbr"))
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
        max_label_len = max(len(l) for l in labels) if labels else 0
        if max_label_len > 8:
            return "hbar"

        # 4. 默认柱状图
        return "bar"
