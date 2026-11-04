# -*- coding: utf-8 -*-
"""风险预警工具：6条核心规则，输入公司名自包含查询"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from agent.tools.base import BaseTool, period_priority, period_priority_sql
from agent.config import get_database_config


class RiskTool(BaseTool):
    name = "risk_analysis"
    description = """财务风险预警分析。输入公司名，自动查询多年数据执行6条风险规则检测。"""
    parameters = {
        "type": "object",
        "properties": {
            "company": {"type": "string", "description": "公司名称"}
        },
        "required": ["company"]
    }
    priority = 50

    def __init__(self):
        self._db_conn = None

    def _get_conn(self):
        if self._db_conn is None:
            import mysql.connector
            db_cfg = get_database_config()
            self._db_conn = mysql.connector.connect(
                host=db_cfg["host"], port=db_cfg["port"],
                database=db_cfg["database"], user=db_cfg["user"],
                password=db_cfg["password"],
            )
        return self._db_conn

    async def execute(self, company: str = "") -> str:
        """异步执行"""
        import json as _json
        result = self.run({"company": company})
        return _json.dumps(result, ensure_ascii=False, default=str)

    def run(self, inputs: dict) -> dict:
        company = inputs.get("company", "")
        year = inputs.get("year")
        period = inputs.get("period")
        if not company:
            return {"success": False, "error": "缺少company参数"}

        try:
            conn = self._get_conn()
            c = conn.cursor(dictionary=True)

            # 查数据（支持指定年份/报告期）
            data = self._query_company_data(c, company, year, period)

            if not data:
                return {"success": False, "error": "未找到 %s 的财务数据" % company}

            # 合并同公司同年同报告期
            merged = self._merge_rows(data)

            # 6条风险规则
            alerts = []
            alerts.extend(self._rule_debt_ratio(merged))
            alerts.extend(self._rule_cashflow(merged))
            alerts.extend(self._rule_receivable(merged))
            alerts.extend(self._rule_inventory(merged))
            alerts.extend(self._rule_profit_decline(merged))
            alerts.extend(self._rule_roe(merged))

            # 去重排序
            alerts = self._deduplicate(alerts)
            severity_order = {"高": 0, "中": 1, "低": 2}
            alerts.sort(key=lambda x: severity_order.get(x.get("severity", "低"), 3))

            c.close()

            return {
                "success": True,
                "company": company,
                "data": merged[:5],
                "alerts": alerts,
                "alert_count": len(alerts),
                "high_count": sum(1 for a in alerts if a["severity"] == "高"),
                "summary": self._generate_summary(company, alerts),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _query_company_data(self, cursor, company, year=None, period=None):
        """查询公司数据。

        year 语义为「截止年」：给定 year 时取该年及以前的数据，使多年趋势类规则
        （现金流连续为负 / 应收·存货同比 / 净利连续下滑）仍可工作；当期口径由报告期
        优先级决定。
        period 传入时为精确报告期过滤。

        N2 修复：原 `ORDER BY report_period DESC` 是字符串序，会排成
        Q3 > Q1 > H1 > FY，导致“当期”取到 Q3；改用 FIELD() 按覆盖度排序。
        """
        queries = [
            ("income_sheet", "stock_abbr, report_year, report_period, net_profit, total_operating_revenue, operating_expense_cost_of_sales"),
            ("balance_sheet", "stock_abbr, report_year, report_period, asset_total_assets, liability_total_liabilities, asset_accounts_receivable, asset_inventory, equity_total_equity"),
            ("cash_flow_sheet", "stock_abbr, report_year, report_period, operating_cf_net_amount"),
        ]

        _porder = period_priority_sql("report_period")
        all_rows = []
        for tbl, cols in queries:
            if year and period:
                sql = ("SELECT %s FROM %s WHERE stock_abbr LIKE %%s AND report_year=%%s "
                       "AND report_period=%%s") % (cols, tbl)
                cursor.execute(sql, ("%" + company + "%", year, period))
            elif year:
                sql = ("SELECT %s FROM %s WHERE stock_abbr LIKE %%s AND report_year <= %%s "
                       "ORDER BY report_year DESC, %s ASC LIMIT 30") % (cols, tbl, _porder)
                cursor.execute(sql, ("%" + company + "%", year))
            else:
                sql = ("SELECT %s FROM %s WHERE stock_abbr LIKE %%s "
                       "ORDER BY report_year DESC, %s ASC LIMIT 30") % (cols, tbl, _porder)
                cursor.execute(sql, ("%" + company + "%",))
            rows = cursor.fetchall()
            all_rows.extend(rows)

        return all_rows

    def _merge_rows(self, data):
        """合并同一公司同年同报告期"""
        merged = {}
        for row in data:
            key = (row.get("stock_abbr", ""), row.get("report_year"), row.get("report_period"))
            if key not in merged:
                merged[key] = {"stock_abbr": key[0], "report_year": key[1], "report_period": key[2]}
            for k, v in row.items():
                if v is not None and k not in merged[key]:
                    merged[key][k] = v

        def _ynum(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return 0

        # N2 修复：不能用 (report_year, report_period) 的字符串序（Q3 > Q1 > H1 > FY），
        # 否则规则 3/4/5 通过“年初第一条”取到的是 Q3 而非 FY。
        result = sorted(
            merged.values(),
            key=lambda x: (_ynum(x.get("report_year")), period_priority(x.get("report_period"))),
            reverse=True,
        )
        return result

    # ==================== 6条风险规则 ====================

    @staticmethod
    def _best_per_year(data):
        """按年归并：每年只保留覆盖度最高的报告期（FY > Q3 > H1 > Q1）。

        N3 修复（延伸）：原「当期水平」类规则（资产负债率、ROE）遍历该年所有报告期，
        导致 2024 年报告里混入 2024Q1 的告警（如 Q1 ROE=4.08% 触发 ROE<5%，
        而全年 ROE 为 12.27%）。改为每年只判当期口径，与报告正文保持一致。
        """
        best = {}
        for row in data:
            yr = row.get("report_year")
            if yr not in best or period_priority(row.get("report_period")) > period_priority(best[yr].get("report_period")):
                best[yr] = row
        return list(best.values())

    def _rule_debt_ratio(self, data):
        """规则1：偿债风险 — 资产负债率 > 70%"""
        alerts = []
        for row in self._best_per_year(data):
            a = self._f(row.get("asset_total_assets"))
            l = self._f(row.get("liability_total_liabilities"))
            if a and l and a > 0:
                ratio = l / a
                if ratio > 0.7:
                    alerts.append({
                        "type": "偿债风险", "rule": "资产负债率>70%", "severity": "高",
                        "detail": "%s %s%s 资产负债率=%.1f%%" % (row["stock_abbr"], row["report_year"], row["report_period"], ratio * 100),
                        "suggestion": "高负债风险",
                    })
        return alerts

    def _rule_cashflow(self, data):
        """规则2：现金流风险 — 经营现金流连续2年为负"""
        alerts = []
        # 按年份分组，取每年最新报告期
        by_year = {}
        for row in data:
            yr = row.get("report_year")
            ocf = self._f(row.get("operating_cf_net_amount"))
            if yr and ocf is not None:
                # N2 修复：原为 report_period 字符串比较（'Q3' > 'FY'），改用覆盖度优先级
                if yr not in by_year or period_priority(row.get("report_period")) > period_priority(by_year[yr][0]):
                    by_year[yr] = (row.get("report_period", ""), ocf)

        years = sorted(by_year.keys(), reverse=True)
        neg_years = [yr for yr in years if by_year[yr][1] < 0]

        if len(neg_years) >= 2:
            alerts.append({
                "type": "现金流风险", "rule": "经营现金流连续%d年为负" % len(neg_years), "severity": "高",
                "detail": "%s %s年经营现金流=%s" % (data[0]["stock_abbr"], neg_years[0], self._fmt(by_year[neg_years[0]][1])),
                "suggestion": "现金流风险",
            })
        return alerts

    def _rule_receivable(self, data):
        """规则3：回款风险 — 应收账款增长率 > 营收增长率"""
        alerts = []
        by_year = {}
        for row in data:
            yr = row.get("report_year")
            if yr not in by_year:
                by_year[yr] = {"ar": self._f(row.get("asset_accounts_receivable")), "rev": self._f(row.get("total_operating_revenue"))}

        years = sorted(by_year.keys())
        for i in range(1, len(years)):
            prev, curr = by_year[years[i-1]], by_year[years[i]]
            ar0, ar1 = prev.get("ar"), curr.get("ar")
            rev0, rev1 = prev.get("rev"), curr.get("rev")
            if not ar0 or not ar1 or not rev0 or not rev1:
                continue
            if ar0 < 1000 or rev0 < 1000:  # 上期值太小跳过
                continue
            ar_growth = (ar1 - ar0) / ar0
            rev_growth = (rev1 - rev0) / rev0
            # 增长率异常大时跳过（数据质量问题）
            if abs(ar_growth) > 10 or abs(rev_growth) > 10:
                continue
            if ar_growth > rev_growth and ar_growth > 0.1:
                alerts.append({
                    "type": "回款风险", "rule": "应收账款增长>营收增长", "severity": "中",
                    "detail": "%s %d年 应收增长=%.1f%% 营收增长=%.1f%%" % (data[0]["stock_abbr"], years[i], ar_growth*100, rev_growth*100),
                    "suggestion": "回款能力下降",
                })
        return alerts

    def _rule_inventory(self, data):
        """规则4：库存风险 — 存货增长率 > 营收增长率"""
        alerts = []
        by_year = {}
        for row in data:
            yr = row.get("report_year")
            if yr not in by_year:
                by_year[yr] = {"inv": self._f(row.get("asset_inventory")), "rev": self._f(row.get("total_operating_revenue"))}

        years = sorted(by_year.keys())
        for i in range(1, len(years)):
            prev, curr = by_year[years[i-1]], by_year[years[i]]
            inv0, inv1 = prev.get("inv"), curr.get("inv")
            rev0, rev1 = prev.get("rev"), curr.get("rev")
            if not inv0 or not inv1 or not rev0 or not rev1:
                continue
            if inv0 < 1000 or rev0 < 1000:  # 上期值太小跳过
                continue
            inv_growth = (inv1 - inv0) / inv0
            rev_growth = (rev1 - rev0) / rev0
            # 增长率异常大时跳过（数据质量问题）
            if abs(inv_growth) > 10 or abs(rev_growth) > 10:
                continue
            if inv_growth > rev_growth and inv_growth > 0.1:
                alerts.append({
                    "type": "库存风险", "rule": "存货增长>营收增长", "severity": "中",
                    "detail": "%s %d年 存货增长=%.1f%% 营收增长=%.1f%%" % (data[0]["stock_abbr"], years[i], inv_growth*100, rev_growth*100),
                    "suggestion": "库存积压风险",
                })
        return alerts

    def _rule_profit_decline(self, data):
        """规则5：盈利风险 — 净利润连续2年下降"""
        alerts = []
        by_year = {}
        for row in data:
            yr = row.get("report_year")
            np_val = self._f(row.get("net_profit"))
            if yr and np_val is not None:
                if yr not in by_year:
                    by_year[yr] = np_val

        years = sorted(by_year.keys(), reverse=True)
        if len(years) >= 2:
            declines = 0
            for i in range(len(years) - 1):
                if by_year[years[i]] < by_year[years[i+1]]:
                    declines += 1

            if declines >= 2:
                alerts.append({
                    "type": "盈利风险", "rule": "净利润连续%d年下降" % declines, "severity": "高",
                    "detail": "%s %s年净利润=%s %s年净利润=%s" % (
                        data[0]["stock_abbr"], years[declines], self._fmt(by_year[years[declines]]),
                        years[0], self._fmt(by_year[years[0]])),
                    "suggestion": "盈利能力下滑",
                })
        return alerts

    def _rule_roe(self, data):
        """规则6：ROE风险 — 使用净利润/净资产计算ROE（当期口径，按年取最高覆盖期）"""
        alerts = []
        for row in self._best_per_year(data):
            np_val = self._f(row.get("net_profit"))
            equity = self._f(row.get("equity_total_equity"))
            if np_val is not None and equity is not None and equity > 0:
                roe = np_val / equity
                if roe < 0.05 and roe > -1:  # 排除异常值
                    alerts.append({
                        "type": "ROE风险", "rule": "ROE<5%", "severity": "低",
                        "detail": "%s %s%s ROE=%.2f%%" % (row["stock_abbr"], row["report_year"], row["report_period"], roe * 100),
                        "suggestion": "股东回报能力较弱",
                    })
        return alerts

    # ==================== 工具方法 ====================

    def _deduplicate(self, alerts):
        seen = set()
        result = []
        for a in alerts:
            key = (a.get("type"), a.get("rule"))
            if key not in seen:
                seen.add(key)
                result.append(a)
        return result

    def _generate_summary(self, company, alerts):
        if not alerts:
            return "%s财务状况良好，6项风险规则均未触发。" % company
        high = [a for a in alerts if a["severity"] == "高"]
        mid = [a for a in alerts if a["severity"] == "中"]
        low = [a for a in alerts if a["severity"] == "低"]
        parts = ["%s风险预警：" % company]
        if high:
            parts.append("高风险%d项：%s" % (len(high), "、".join(a["suggestion"] for a in high)))
        if mid:
            parts.append("中风险%d项：%s" % (len(mid), "、".join(a["suggestion"] for a in mid)))
        if low:
            parts.append("低风险%d项：%s" % (len(low), "、".join(a["suggestion"] for a in low)))
        return "；".join(parts)

    @staticmethod
    def _f(val):
        if val is None: return None
        try: return float(val)
        except: return None

    @staticmethod
    def _fmt(val):
        if val is None: return "N/A"
        if abs(val) >= 10000: return "%.2f亿" % (val / 10000)
        return "%.2f万" % val
