# -*- coding: utf-8 -*-
"""SQL查询工具：封装pipeline.query() + 趋势/对比专用查询"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from agent.tools.base import BaseTool

PERIOD_PRIORITY = {"FY": 4, "Q3": 3, "H1": 2, "Q1": 1}
PERIOD_FALLBACK = ["FY", "Q3", "H1", "Q1"]


class SQLTool(BaseTool):
    name = "sql_query"
    description = """
    查询财务数据库。
    用于：查具体数值（营收、净利润、总资产、负债、现金流等）
    输入：question(自然语言问题)
    返回：查询结果数据
    不用于：分析原因、查研报内容、计算派生指标
    """

    def __init__(self):
        self._pipeline = None
        self._db_conn = None

    def _get_pipeline(self):
        if self._pipeline is None:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "models", "model2_question_understanding"))
            from pipeline import FinancialQAPipeline
            self._pipeline = FinancialQAPipeline()
        return self._pipeline

    def _get_conn(self):
        if self._db_conn is None:
            import mysql.connector
            self._db_conn = mysql.connector.connect(
                host="localhost", port=3306, database="finance_data",
                user="root", password="433127hj"
            )
        return self._db_conn

    def run(self, inputs: dict) -> dict:
        question = inputs.get("question", "")
        query_type = inputs.get("query_type", "auto")

        if not question:
            return {"success": False, "error": "缺少question参数"}

        try:
            if query_type == "trend":
                return self._query_trend(inputs)
            elif query_type == "compare":
                return self._query_compare(inputs)
            else:
                return self._query_auto(question)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _query_auto(self, question):
        """标准查询：走pipeline"""
        pipeline = self._get_pipeline()
        result = pipeline.query(question)
        data = result.get("data")
        # 数据库单位统一为万元
        unit = "万元" if data else ""
        return {
            "success": result.get("success", False),
            "data": data,
            "sql": result.get("sql"),
            "answer": result.get("answer"),
            "confidence": result.get("confidence", 0),
            "slots": result.get("slots", {}),
            "unit": unit,
        }

    def _query_trend(self, inputs):
        """趋势查询：每年只返回一条，优先级 FY > Q3 > H1 > Q1"""
        metric = inputs.get("metric", "net_profit")
        company = inputs.get("company", "")
        year_start = inputs.get("year_start", 2022)
        year_end = inputs.get("year_end", 2026)

        if not company:
            return {"success": False, "error": "缺少company参数"}

        conn = self._get_conn()
        c = conn.cursor(dictionary=True)

        # 查所有表
        tables = ["income_sheet", "core_performance_indicators_sheet"]
        all_rows = []
        for tbl in tables:
            sql = "SELECT stock_abbr, report_year, report_period, %s FROM %s WHERE stock_abbr LIKE %%s AND report_year BETWEEN %%s AND %%s AND %s IS NOT NULL" % (metric, tbl, metric)
            c.execute(sql, ("%" + company + "%", year_start, year_end))
            all_rows.extend(c.fetchall())
        c.close()

        # 每年只保留优先级最高的报告期
        best_per_year = {}
        for row in all_rows:
            year = row["report_year"]
            period = row.get("report_period", "")
            priority = PERIOD_PRIORITY.get(period, 0)
            if year not in best_per_year or priority > PERIOD_PRIORITY.get(best_per_year[year].get("report_period", ""), 0):
                best_per_year[year] = row

        result = sorted(best_per_year.values(), key=lambda x: x["report_year"])

        # 标注非年报数据
        for row in result:
            if row.get("report_period") != "FY":
                row["note"] = "注：%d年无年报，使用%s数据" % (row["report_year"], row.get("report_period", ""))

        return {
            "success": bool(result),
            "data": result,
            "query_type": "trend",
            "company": company,
            "metric": metric,
        }

    def _query_compare(self, inputs):
        """对比查询：每家公司独立查，各自降级"""
        metric = inputs.get("metric", "total_operating_revenue")
        companies = inputs.get("companies", [])
        year = inputs.get("year", 2024)

        if not companies:
            return {"success": False, "error": "缺少companies参数"}

        conn = self._get_conn()
        c = conn.cursor(dictionary=True)

        results = []
        for company in companies:
            row = None
            used_period = None

            # 按优先级逐个尝试
            for period in PERIOD_FALLBACK:
                for tbl in ["income_sheet", "core_performance_indicators_sheet"]:
                    sql = "SELECT stock_abbr, report_year, report_period, %s FROM %s WHERE stock_abbr LIKE %%s AND report_year = %%s AND report_period = %%s AND %s IS NOT NULL LIMIT 1" % (metric, tbl, metric)
                    c.execute(sql, ("%" + company + "%", year, period))
                    row = c.fetchone()
                    if row:
                        used_period = period
                        break
                if row:
                    break

            if row:
                if used_period != "FY":
                    row["note"] = "注：%d年无年报，使用%s数据" % (year, used_period)
                results.append(row)
            else:
                results.append({
                    "stock_abbr": company,
                    "report_year": year,
                    metric: None,
                    "note": "注：%d年无数据" % year,
                })

        c.close()

        return {
            "success": bool(results),
            "data": results,
            "query_type": "compare",
            "companies": companies,
            "metric": metric,
        }
