# -*- coding: utf-8 -*-
"""SQL Generator v3 - 支持多字段/多公司/跨表JOIN"""
import os
import re
import sys
from datetime import datetime
from time_parser import (parse_time as _parse_time,
                         apply_time_to_sql as _apply_time,
                         PERIOD_KEYWORDS as _PERIOD_KEYWORDS)


def _ensure_repo_root_on_path():
    """把仓库根加入 sys.path，使 `import domain.*` 可用（不依赖 cwd）。"""
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(4):
        if os.path.isdir(os.path.join(d, "domain", "normalize")):
            if d not in sys.path:
                sys.path.insert(0, d)
            return d
        d = os.path.dirname(d)
    return ""


_ensure_repo_root_on_path()

# 公司别名：唯一真相源 domain/normalize/company_names.py
# （历史：本文件另存一份 6 条 COMPANY_ALIAS，与 rag_pipeline / pipeline 的副本并存）
from domain.normalize.company_names import ALIASES as COMPANY_ALIAS  # noqa: E402

# report_period 的合法取值 —— 2026-09-14 实测 core_performance_indicators_sheet
# 仅有 FY/Q3/Q1/H1（'HY' 0 行、'中报' 0 行）。历史 PERIOD_MAP 把
# H1 / 半年报 / 半年度 / 上半年 一律映射成 'HY'，导致「上半年/半年报」类
# 提问必然查空（N8）。现改为统一走 time_parser.PERIOD_KEYWORDS 并用本元组兜底。
VALID_PERIOD_CODES = ("FY", "Q3", "Q2", "Q1", "H1")

# 兼容旧引用名：内容即 SSOT 词表，不再是独立副本
PERIOD_MAP = dict(_PERIOD_KEYWORDS)

FUZZY_CONDITIONS = {
    "亏钱": ("net_profit", "<", 0), "亏损": ("net_profit", "<", 0),
    "盈利": ("net_profit", ">", 0), "赚钱": ("net_profit", ">", 0),
    "高负债": ("asset_liability_ratio", ">", 60), "低负债": ("asset_liability_ratio", "<", 30),
}

# 操作符映射
OPERATOR_MAP = {
    "大于": ">", "小于": "<", "不低于": ">=", "不高于": "<=",
    "以上": ">=", "以下": "<=", "超过": ">", "不足": "<",
    "高于": ">", "低于": "<", "不低于": ">=", "不高于": "<=",
}

FIELD_TABLES = {
    # income_sheet
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
    # balance_sheet
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
    # cash_flow_sheet
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
    # core_performance_indicators_sheet（ROE、EPS等指标）
    "total_share_capital": ["core_performance_indicators_sheet"],
    "eps": ["core_performance_indicators_sheet"],
    "operating_revenue_yoy_growth": ["core_performance_indicators_sheet"],
    "operating_revenue_qoq_growth": ["core_performance_indicators_sheet"],
    "net_profit_yoy_growth": ["core_performance_indicators_sheet"],
    "net_profit_qoq_growth": ["core_performance_indicators_sheet"],
    "net_asset_per_share": ["core_performance_indicators_sheet"],
    "roe": ["core_performance_indicators_sheet"],
    "operating_cf_per_share": ["core_performance_indicators_sheet"],
    "net_profit_excl_non_recurring": ["core_performance_indicators_sheet"],
    "net_profit_excl_non_recurring_yoy": ["core_performance_indicators_sheet"],
    "gross_profit_margin": ["core_performance_indicators_sheet"],
    "net_profit_margin": ["core_performance_indicators_sheet"],
    "roe_weighted_excl_non_recurring": ["core_performance_indicators_sheet"],
}

CURRENT_YEAR = datetime.now().year
# 注：COMPANY_ALIAS 已在上方从 domain/normalize/company_names.py 导入，
# 此处不再保留副本（原为 6 条内联字典，会覆盖导入值 —— 2026-09-14 删除）。

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
        """把中文报告期词/英文缩写归一为 **DB 实际取值**（FY/Q3/Q2/Q1/H1）。

        词表唯一真相源 = time_parser.PERIOD_KEYWORDS；结果必须落在
        VALID_PERIOD_CODES 内，否则视为"无合法报告期"返回 None（不加该过滤），
        避免再拿非法值查库（N8：'HY' 使半年报查询恒空）。
        """
        # 1) 已是合法 DB 取值
        if period in VALID_PERIOD_CODES:
            return period
        # 2) 中文词/缩写 → 词表
        if period and period in _PERIOD_KEYWORDS:
            code = _PERIOD_KEYWORDS[period]
            if code in VALID_PERIOD_CODES:
                return code
        # 3) 从问题里找报告期词（长词优先，避免"三季度"被更短词抢先）
        q = question or ""
        for kw in sorted(_PERIOD_KEYWORDS, key=len, reverse=True):
            if kw in q:
                code = _PERIOD_KEYWORDS[kw]
                if code in VALID_PERIOD_CODES:
                    return code
        # 4) 无合法报告期
        return None

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
                return (field, op, val)
        return None

    def _extract_filter_condition(self, question, col):
        """
        从问题中提取动态筛选条件（如 "超过15%"）
        返回格式: (field, operator, value) 或 None
        """
        # 检查百分比形式（如 "15%"）- 优先匹配
        percent_match = re.search(r'(\d+\.?\d*)\s*%', question)
        if percent_match:
            value = percent_match.group(1)
            # 检查操作符
            for op_word, op_symbol in OPERATOR_MAP.items():
                if op_word in question:
                    return (col, op_symbol, value)
            # 默认 > 
            return (col, ">", value)

        # 检查常见操作符
        for op_word, op_symbol in OPERATOR_MAP.items():
            if op_word in question:
                # 提取数值，但要排除年份（20开头的4位数字）
                nums = re.findall(r'\d+\.?\d*', question)
                for num in nums:
                    # 排除年份（2024等）
                    if len(num) == 4 and num.startswith('20'):
                        continue
                    return (col, op_symbol, num)

        return None

    def _build_where_clause(self, company_cond, time_where, period, col, cond_clause, main_table=None):
        """构建WHERE子句"""
        prefix = (main_table + ".") if main_table else ""
        where_parts = []
        if company_cond:
            if main_table:
                # 跨表 JOIN：stock_abbr / stock_code 两张表都有，
                # 裸列触发 MySQL 1052（实测：多指标跨表查询直接失败）
                company_cond = (company_cond
                                .replace("stock_abbr", prefix + "stock_abbr")
                                .replace("stock_code", prefix + "stock_code"))
            where_parts.append(company_cond)
        if time_where:
            # 跨表JOIN时需要加表名前缀
            qualified_time = time_where.replace("report_year", prefix + "report_year") if main_table else time_where
            where_parts.append(qualified_time)
        if period:
            where_parts.append(prefix + "report_period = '" + period + "'")
        if cond_clause:
            if main_table:
                # cond_clause 恒以 col 开头（col=cols[0]，必属 main_table），
                # 与 company_cond 同类隐患：若 col 同时存在于被 JOIN 表则歧义
                cond_clause = prefix + cond_clause
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

        # ===== 提取筛选条件（新增）=====
        filter_condition = None
        if question:
            # 先检查模糊条件（如 "亏钱"、"盈利"）
            fuzzy_cond = self.get_fuzzy_condition(question)
            if fuzzy_cond:
                filter_condition = fuzzy_cond
            else:
                # 尝试提取动态条件（如 "> 15%"）
                filter_condition = self._extract_filter_condition(question, col)

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

        # ===== 构建WHERE子句（包含筛选条件）=====
        cond_clause = None
        if filter_condition:
            field, op, value = filter_condition
            # 如果是百分比字段（ROE等），可能需要判断单位
            # ROE 在数据库中是百分比形式（如 15 表示 15%）
            # 检查是否是百分比字段
            is_percent_field = any(kw in col for kw in ["margin", "ratio", "roe", "growth"])
            if is_percent_field and float(value) < 100:
                # 百分比字段，直接用原值（如 15 表示 15%）
                cond_clause = f"{col} {op} {value}"
            else:
                cond_clause = f"{col} {op} {value}"

        where_str = self._build_where_clause(company_cond, time_where, period, col, cond_clause)

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
                
                where_str = self._build_where_clause(company_cond, time_where, period, col, cond_clause, main_table)
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
            # N5 修复（续）：单公司 basic_query 无显式排序时，补“年份降序 +
            # 报告期覆盖度”（FY > Q3 > H1 > Q1）——否则库返回行序不定，
            # 同一公司 3 年 × 4 报告期多行挤在一起，答案正确性靠 LLM 自己挑。
            if not order_clause and intent == 'basic_query' and company:
                order_clause = ("ORDER BY report_year DESC, "
                                "FIELD(report_period,'FY','Q3','H1','Q1') ASC")
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
            # N2 修复：report_period 是字符串列（'FY'/'Q3'/'H1'/'Q1'），
            # `report_period DESC` 会排成 Q3 > Q1 > H1 > FY，使单公司多期查询把 Q3
            # 排在 FY 之前、取错报告期。改用 FIELD() 按覆盖度排序（口径与
            # agent.tools.base.PERIOD_ORDER 一致：FY > Q3 > H1 > Q1）。
            outer.append("ORDER BY report_year DESC, FIELD(report_period,'FY','Q3','H1','Q1') ASC")
        elif order_clause:
            outer.append(order_clause)
        if limit_clause:
            outer.append(limit_clause)
        return '\n'.join(outer) + ';'
