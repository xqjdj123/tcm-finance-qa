# -*- coding: utf-8 -*-
"""SQL Generator v3 - 支持多字段/多公司/跨表JOIN"""
import re
from datetime import datetime
from time_parser import parse_time as _parse_time, apply_time_to_sql as _apply_time

PERIOD_MAP = {
    "前三季度": "Q3", "三季度": "Q3", "第三季度": "Q3", "Q3": "Q3",
    "半年报": "HY", "半年度": "HY", "上半年": "HY", "HY": "HY", "H1": "HY",
    "一季报": "Q1", "一季度": "Q1", "第一季度": "Q1", "Q1": "Q1",
    "年报": "FY", "年度": "FY", "全年": "FY", "FY": "FY",
    "二季报": "Q2", "二季度": "Q2", "Q2": "Q2",
}

FUZZY_CONDITIONS = {
    "亏钱": ("net_profit", "<", 0), "亏损": ("net_profit", "<", 0),
    "盈利": ("net_profit", ">", 0), "赚钱": ("net_profit", ">", 0),
    "高负债": ("asset_liability_ratio", ">", 60), "低负债": ("asset_liability_ratio", "<", 30),
}

FIELD_TABLES = {
    "total_operating_revenue": ["income_sheet"],
    "operating_expense_cost_of_sales": ["income_sheet"],
    "operating_expense_selling_expenses": ["income_sheet"],
    "operating_expense_administrative_expenses": ["income_sheet"],
    "operating_expense_financial_expenses": ["income_sheet"],
    "operating_expense_rnd_expenses": ["income_sheet"],
    "operating_expense_taxes_and_surcharges": ["income_sheet"],
    "total_operating_expenses": ["income_sheet"],
    "operating_profit": ["income_sheet"],
    "total_profit": ["income_sheet"],
    "net_profit": ["income_sheet"],
    "asset_impairment_loss": ["income_sheet"],
    "credit_impairment_loss": ["income_sheet"],
    "other_income": ["income_sheet"],
    "asset_cash_and_cash_equivalents": ["balance_sheet"],
    "asset_accounts_receivable": ["balance_sheet"],
    "asset_inventory": ["balance_sheet"],
    "asset_trading_financial_assets": ["balance_sheet"],
    "asset_construction_in_progress": ["balance_sheet"],
    "asset_total_assets": ["balance_sheet"],
    "asset_total_assets_yoy_growth": ["balance_sheet"],
    "liability_accounts_payable": ["balance_sheet"],
    "liability_advance_from_customers": ["balance_sheet"],
    "liability_contract_liabilities": ["balance_sheet"],
    "liability_short_term_loans": ["balance_sheet"],
    "liability_total_liabilities": ["balance_sheet"],
    "liability_total_liabilities_yoy_growth": ["balance_sheet"],
    "asset_liability_ratio": ["balance_sheet"],
    "equity_unappropriated_profit": ["balance_sheet"],
    "equity_total_equity": ["balance_sheet"],
    "net_cash_flow": ["cash_flow_sheet"],
    "net_cash_flow_yoy_growth": ["cash_flow_sheet"],
    "operating_cf_net_amount": ["cash_flow_sheet"],
    "operating_cf_ratio_of_net_cf": ["cash_flow_sheet"],
    "operating_cf_cash_from_sales": ["cash_flow_sheet"],
    "investing_cf_net_amount": ["cash_flow_sheet"],
    "investing_cf_ratio_of_net_cf": ["cash_flow_sheet"],
    "investing_cf_cash_for_investments": ["cash_flow_sheet"],
    "investing_cf_cash_from_investment_recovery": ["cash_flow_sheet"],
    "financing_cf_cash_from_borrowing": ["cash_flow_sheet"],
    "financing_cf_cash_for_debt_repayment": ["cash_flow_sheet"],
    "financing_cf_net_amount": ["cash_flow_sheet"],
    "financing_cf_ratio_of_net_cf": ["cash_flow_sheet"],
}

CURRENT_YEAR = datetime.now().year
COMPANY_ALIAS = {
    "999": "华润三九",
    "三九": "华润三九",
    "白药": "云南白药",
    "金花": "金花股份",
    "同仁": "同仁堂",
    "白云": "白云山",
    "三金": "桂林三金",
}

CODE_TO_NAME = {}

def build_company_condition(company):
    """生成公司名条件（stock_abbr已归一化为中文名，直接查）"""
    # 先查别名
    real_name = COMPANY_ALIAS.get(company, company)
    return "(stock_abbr = '" + real_name + "' OR stock_code = '" + real_name + "' OR stock_abbr LIKE '%" + real_name + "%')"


class SQLGenerator:
    def __init__(self):
        pass

    def normalize_period(self, period, question):
        if period and period in PERIOD_MAP:
            return PERIOD_MAP[period]
        for k, v in PERIOD_MAP.items():
            if k in question:
                return v
        return period

    def parse_condition(self, condition, col, question):
        if not condition:
            return None
        cond_map = {
            "大于": ">", "小于": "<", "不低于": ">=", "不高于": "<=",
            "以上": ">=", "以下": "<=", "超过": ">", "不足": "<",
        }
        for kw, op in cond_map.items():
            if kw in condition:
                nums = re.findall(r"\d+\.?\d*", condition)
                if nums:
                    return col + " " + op + " " + nums[0]
        return None

    def get_fuzzy_condition(self, question):
        for kw, (field, op, val) in FUZZY_CONDITIONS.items():
            if kw in question:
                return field + " " + op + " " + str(val)
        return None

    def _build_where_clause(self, company_cond, time_where, period, col, cond_clause, main_table=None):
        """构建WHERE子句"""
        prefix = (main_table + ".") if main_table else ""
        where_parts = []
        if company_cond:
            where_parts.append(company_cond)
        if time_where:
            # 跨表JOIN时需要加表名前缀
            qualified_time = time_where.replace("report_year", prefix + "report_year") if main_table else time_where
            where_parts.append(qualified_time)
        if period:
            where_parts.append(prefix + "report_period = '" + period + "'")
        if cond_clause:
            where_parts.append(cond_clause)
        if not where_parts:
            return ""
        return "WHERE " + "\n  AND ".join(where_parts)

    def _resolve_table(self, col_name):
        """获取字段的候选表列表"""
        tables = FIELD_TABLES.get(col_name, [])
        if not tables:
            return ["core_performance_indicators_sheet"]
        return tables

    def generate(self, understanding, matches, question=""):
        """主入口：支持多字段/多公司/跨表JOIN"""
        intent = understanding.get("intent", "basic_query")
        company = understanding.get("company")
        year = understanding.get("year")
        period = understanding.get("period")
        top_k = understanding.get("top_k")
        condition = understanding.get("condition")
        companies = understanding.get("companies", [])
        indicators = understanding.get("indicators", [])
        is_multi_indicator = understanding.get("is_multi_indicator", False)
        is_multi_company = understanding.get("is_multi_company", False)

        if not matches:
            return "-- 未匹配到字段"

        # 提取所有匹配字段的列名
        cols = [m["column_en"] for m in matches]
        col = cols[0]  # 主排序列

        period = self.normalize_period(period, question)

        # ===== 时间条件（直接用 understanding 里的 year/period，不再重新解析）=====
        time_where = ''
        time_order = ''
        time_limit = ''
        if year:
            time_where = 'report_year = %d' % year
        if intent == "time_trend":
            # 趋势查询：year 作为起始年，查到最新
            if year:
                time_where = 'report_year >= %d' % year
            time_order = 'ORDER BY report_year'
        if intent in ("stat_query", "fuzzy_intent"):
            time_order = 'ORDER BY %s DESC' % col
            time_limit = 'LIMIT 10'
        if top_k:
            time_order = 'ORDER BY %s DESC' % col
            time_limit = 'LIMIT %d' % top_k

        # ===== 多公司条件 =====
        if len(companies) >= 2:
            # 多公司：用OR连接
            conds = []
            for c in companies:
                conds.append(build_company_condition(c))
            company_cond = "(" + " OR ".join(conds) + ")"
        elif company:
            # 单公司
            company_cond = build_company_condition(company)
        else:
            company_cond = None

        # ===== 多指标 == 跨表检测 =====
        # 检查所有字段是否在同一张表
        all_tables = set()
        for c in cols:
            for t in self._resolve_table(c):
                all_tables.add(t)

        # ===== 排序和LIMIT =====
        order_by = col  # 默认按第一个字段排序
        order_clause = ""
        if time_order:
            order_clause = time_order
        elif top_k:
            order_clause = "ORDER BY " + order_by + " DESC"
        elif intent in ["stat_query", "fuzzy_intent"]:
            order_clause = "ORDER BY " + order_by + " DESC"

        limit_clause = ""
        if time_limit:
            limit_clause = time_limit
        elif top_k:
            limit_clause = "LIMIT " + str(top_k)
        elif intent in ["stat_query", "fuzzy_intent"]:
            limit_clause = "LIMIT 10"

        # ===== SELECT 列 =====
        if len(cols) == 1:
            # 单字段
            if top_k or intent in ["stat_query", "fuzzy_intent", "comparison"]:
                select_cols = "stock_abbr, report_year, report_period, " + col
            elif intent == "time_trend":
                select_cols = "report_year, " + col + ", stock_abbr, report_period"
            else:
                select_cols = col + ", stock_abbr, report_year, report_period"
        else:
            # 多字段：加上表别名防止歧义，包含 report_period 用于去重
            select_cols = "stock_abbr, report_year, report_period, " + ", ".join(cols)

                
        # ===== 构建WHERE子句 =====
        where_str = self._build_where_clause(company_cond, time_where, period, col, None)
# ===== 决定查询策略 =====
        # 策略1: 跨表JOIN（多字段来自不同表）
        # 策略2: UNION （单字段但候选表有多个）
        # 策略3: 单表查询（所有字段在同一张表）
        
        if len(cols) >= 2 and len(all_tables) >= 2:
            # 检查是否真的需要跨表：不同字段来自不同表
            field_tables = {}
            for c in cols:
                field_tables[c] = self._resolve_table(c)
            
            # 判断是否所有字段都能共存在同一张表
            common_tables = set(field_tables[cols[0]])
            for c in cols[1:]:
                common_tables = common_tables & set(field_tables[c])
            
            if not common_tables:
                # 必须跨表JOIN
                main_table = self._resolve_table(cols[0])[0]
                other_tables = set()
                field_alias = {}
                for c in cols:
                    tbl = self._resolve_table(c)[0]
                    field_alias[c] = tbl
                    if tbl != main_table:
                        other_tables.add(tbl)
                
                aliased_cols = []
                for c in cols:
                    tbl = field_alias[c]
                    aliased_cols.append(tbl + '.' + c)
                select_str = main_table + '.stock_abbr, ' + main_table + '.report_year, ' + ', '.join(aliased_cols)
                
                sql_parts = ['SELECT ' + select_str, 'FROM ' + main_table]
                for ot in other_tables:
                    sql_parts.append('JOIN ' + ot + ' ON ' + main_table + '.stock_code = ' + ot + '.stock_code'
                        + ' AND ' + main_table + '.report_year = ' + ot + '.report_year'
                        + ' AND ' + main_table + '.report_period = ' + ot + '.report_period')
                
                where_str = self._build_where_clause(company_cond, time_where, period, col, None, main_table)
                if where_str:
                    sql_parts.append(where_str)
                
                # IS NOT NULL只检查主表
                nn_checks = [main_table + '.' + cols[0] + ' IS NOT NULL']
                if where_str:
                    sql_parts.append('AND ' + ' AND '.join(nn_checks))
                else:
                    sql_parts.append('WHERE ' + ' AND '.join(nn_checks))
                
                if order_clause:
                    sql_parts.append(order_clause)
                if limit_clause:
                    sql_parts.append(limit_clause)
                return '\n'.join(sql_parts) + ';'
            else:
                # 所有字段能共存于同一张表 → 降级为单表查询
                candidate_tables = list(common_tables)
        else:
            # 单字段或多字段但都在同一表候选集中
            candidate_tables = list(all_tables) if all_tables else self._resolve_table(col)
        
        # ===== 单表查询 =====
        if len(candidate_tables) == 1:
            tbl = candidate_tables[0]
            sql_parts = ['SELECT ' + select_cols, 'FROM ' + tbl]
            if where_str:
                sql_parts.append(where_str)
            # 单列时检查该列非空，多列时用 OR 检查（任一列有数据即可）
            if len(cols) == 1:
                nn_check = cols[0] + ' IS NOT NULL'
            else:
                nn_check = '(' + ' IS NOT NULL OR '.join(cols) + ' IS NOT NULL)'
            if where_str:
                sql_parts.append('AND ' + nn_check)
            else:
                sql_parts.append('WHERE ' + nn_check)
            if order_clause:
                sql_parts.append(order_clause)
            if limit_clause:
                sql_parts.append(limit_clause)
            return '\n'.join(sql_parts) + ';'
        
        # ===== 多表UNION + 去重 =====
        # 给每个子查询加优先级：income_sheet=1, balance_sheet=2, cash_flow_sheet=3
        union_parts = []
        for idx, tbl in enumerate(candidate_tables):
            if tbl == 'income_sheet':
                priority = 1
            elif tbl == 'balance_sheet':
                priority = 2
            elif tbl == 'cash_flow_sheet':
                priority = 3
            else:
                priority = 4
            sub = 'SELECT ' + select_cols + ', ' + str(priority) + ' AS _tbl_priority FROM ' + tbl
            if where_str:
                sub += '\n' + where_str
            union_parts.append(sub)

        full_sql = ' UNION '.join(union_parts)
        # 用 ROW_NUMBER 按公司+报告期去重，保留优先级最高的（income_sheet > core_performance）
        # 多列时用 OR 检查非空（任一列有数据即可）
        if len(cols) == 1:
            nn_check = col + ' IS NOT NULL'
        else:
            nn_check = '(' + ' IS NOT NULL OR '.join(cols) + ' IS NOT NULL)'
        outer = [
            'SELECT ' + select_cols + ' FROM (',
            '  SELECT *, ROW_NUMBER() OVER (PARTITION BY stock_abbr, report_year, report_period ORDER BY _tbl_priority) AS _rn',
            '  FROM (' + full_sql + ') AS t',
            '  WHERE ' + nn_check,
            ') AS t',
            'WHERE _rn = 1',
        ]
        if intent == 'basic_query' and company:
            outer.append('ORDER BY report_year DESC, report_period DESC')
        elif order_clause:
            outer.append(order_clause)
        if limit_clause:
            outer.append(limit_clause)
        return '\n'.join(outer) + ';'
