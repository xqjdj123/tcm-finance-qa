# -*- coding: utf-8 -*-
"""SQL查询工具：封装pipeline.query() + 趋势/对比专用查询"""
import sys, os, re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "models", "model2_question_understanding"))
from agent.tools.base import (
    BaseTool,
    PERIOD_PRIORITY,
    PERIOD_ORDER,
    period_priority,
    pick_best_period,
    period_priority_sql,
)
from agent.config import get_database_config

# 公司名/别名识别的**唯一事实源**（本工具不再自带拷贝）。
# 历史：本文件 `_keyword_understand` 内曾内联 41 家公司的局部列表，
# 与 pipeline.py / session.py / rag_pipeline/config.py 三处并存，
# 逐次漂移（alias 表各自缺项、还混进了 999/白药/花 这类别名）。
# 2026-09-14 收敛到 domain/normalize/company_names.py。
from domain.normalize.company_names import find_all as find_all_companies
from domain.normalize.query_slots import parse_top_k

# 兼容旧引用名：等价于 PERIOD_ORDER（FY > Q3 > H1 > Q1）
PERIOD_FALLBACK = PERIOD_ORDER


class SQLTool(BaseTool):
    name = "sql_query"
    description = """查询财务数据库。用于：查具体数值（营收、净利润、总资产等）"""
    parameters = {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "用户原始问题或查询描述"},
            "understanding": {"type": "object", "description": "可选：pipeline.understand() 的理解结果"},
            "query_type": {"type": "string", "enum": ["auto", "trend", "compare"], "description": "查询类型：auto自动判断"}
        },
        "required": ["question"]
    }
    priority = 100

    # 指标词里的“任务词/语气后缀”（N6）。
    # 抽取层有时把“指标 + 任务词”整串当指标吐出——实测问
    # 「云南白药和片仔癀2024年营收对比」得到 indicators=['营收对比']，
    # 而 field_matcher.match('营收对比') 返回 None（match('营收') 才是满分），
    # 使 compare 分支直接以 field_not_found 失败。
    # 仅作【兜底】重试：先按原词匹配，失败后才削后缀，不会误伤正常指标名。
    TASK_WORD_SUFFIXES = (
        "对比", "比较", "排名", "排行", "趋势", "走势", "变化", "情况",
        "多少", "是多少", "是多少钱", "查询", "查一下", "怎么样", "如何",
    )

    # 关键词兜底表：语义兜底的最后一层（原为 _keyword_understand 内局部变量，
    # 提取到类级以便 _fallback_indicator_matches 复用，避免两套口径）。
    KEYWORD_TO_FIELD = {
        "营收": "total_operating_revenue",
        "营业收入": "total_operating_revenue",
        "收入": "total_operating_revenue",
        "净利润": "net_profit",
        "净利": "net_profit",
        "利润": "total_profit",
        "总资产": "asset_total_assets",
        "资产": "asset_total_assets",
        "毛利率": "gross_profit_margin",
        "净利率": "net_profit_margin",
        "现金流": "operating_cf_net_amount",
        "资产负债率": "asset_liability_ratio",
        "负债": "liability_total_liabilities",
        "研发": "operating_expense_rnd_expenses",
    }

    # 列存在性检查的候选表（跨表定位用）
    CANDIDATE_TABLES = ("income_sheet", "balance_sheet", "cash_flow_sheet",
                        "core_performance_indicators_sheet")

    # ---- 意图关键词表（仅 _keyword_understand 兜底层使用）----
    # 明确表示"要比一比"的词。**不得把「和 / 与」这类连接词单独当信号**：
    # 「云南白药和他的子公司」「2024年前五的中药企业和行业均值」都会命中，
    # 且它排在 stat_query 之后会**覆盖**排名意图（把排名题判成对比题）。
    COMPARISON_KEYWORDS = ("对比", "比较", "vs", "区别", "差异", "相比")
    TREND_KEYWORDS = ("趋势", "走势", "变化", "近几年", "近三年", "近五年",
                      "逐年", "历年", "逐年变化")

    def _looks_like_comparison(self, question, companies) -> bool:
        """对比意图判定：**明确的对比词**，或识别出 ≥2 家公司。

        两个条件都与具体公司/年份无关，属语义判据而非字符串巧合。
        """
        q = (question or "").lower()
        if any(kw in q for kw in self.COMPARISON_KEYWORDS):
            return True
        return len(companies or []) >= 2

    def __init__(self):
        self._pipeline = None
        self._db_conn = None
        self._column_cache = None  # 启动时懒加载，缓存所有表的列名

    def _get_pipeline(self):
        if self._pipeline is None:
            from pipeline import FinancialQAPipeline
            self._pipeline = FinancialQAPipeline()
        return self._pipeline

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

    # ========== 启动时列名缓存（一次性加载，不运行时查 information_schema）==========
    def refresh_column_cache(self):
        """强制刷新列名缓存（新建表后调用）"""
        self._column_cache = None
        self._ensure_column_cache()

    def _ensure_column_cache(self):
        if self._column_cache is not None:
            return
        cache = {}
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            schema = get_database_config()["database"]
            cursor.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = %s",
                (schema,),
            )
            for table_name, column_name in cursor.fetchall():
                if table_name not in cache:
                    cache[table_name] = set()
                cache[table_name].add(column_name)
            cursor.close()
            self._column_cache = cache
            print(f"[SQLTool] Column cache loaded: {sum(len(v) for v in cache.values())} columns across {len(cache)} tables")
        except Exception as e:
            print(f"[SQLTool] Column cache load failed: {e}")
            self._column_cache = {}  # 空缓存，后续检查都会返回不存在

    def _column_exists(self, table, column):
        """查缓存，不访问数据库"""
        self._ensure_column_cache()
        return column in self._column_cache.get(table, set())

    def _execute_sql(self, sql):
        """执行 SQL，返回结果列表或 None"""
        if not sql or sql.startswith("--"):
            return None
        try:
            conn = self._get_conn()
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql)
            results = cursor.fetchall()
            cursor.close()
            return results if results else None
        except Exception as e:
            print(f"  [SQLTool] SQL执行错误: {e}")
            return None

    def _list_available_tables(self, metric_en):
        """检查指标可能存在于哪些表（基于 field_matcher 的 table + 备选表）"""
        candidates = [metric_en]
        tables_checked = ["income_sheet", "balance_sheet", "cash_flow_sheet"]
        available = []
        for tbl in tables_checked:
            for col in candidates:
                if self._column_exists(tbl, col):
                    available.append(tbl)
                    break
            # 也检查列名本身
            if self._column_exists(tbl, metric_en):
                if tbl not in available:
                    available.append(tbl)
        return available

    # ========== 指标词兜底解析（N6）==========
    def _strip_task_words(self, text):
        """把“指标 + 任务词”混合串削成纯指标词，供兜底重试匹配。

        例：'营收对比' -> '营收'；'净利润趋势' -> '净利润'。
        循环削，处理 '营收对比情况' 这类叠加后缀；宁可少削也不削空。
        """
        t = (text or "").strip()
        changed = True
        while changed and t:
            changed = False
            for w in self.TASK_WORD_SUFFIXES:
                if len(t) > len(w) and t.endswith(w):
                    t = t[:-len(w)].strip()
                    changed = True
                    break
        return t

    def _column_ok(self, match):
        """校验匹配结果的列在库中真实存在；表未知时跨候选表定位并回填表名。"""
        col = match.get("column_en")
        if not col:
            return False
        tbl = match.get("table_name")
        if tbl and self._column_exists(tbl, col):
            return True
        for t in self.CANDIDATE_TABLES:
            if self._column_exists(t, col):
                match["table_name"] = t
                return True
        return False

    def _fallback_indicator_matches(self, indicator, question=""):
        """直配失败时的指标兜底解析，返回 match dict 列表（形状同 schema_mapping.match）。

        逐级兜底，任一级命中即返回：
          1. 削掉任务词后缀后重配（N6 主修：'营收对比' -> '营收'）
          2. 整句同义词扫描（match_from_question，按分数降序取前 3）
          3. 关键词表（KEYWORD_TO_FIELD）

        全部失败返回空列表，调用方据此走原有“未匹配到字段”分支。
        """
        from schema_mapping import match as _schema_match

        # 1) 削任务词后缀重配
        stripped = self._strip_task_words(indicator)
        if stripped and stripped != indicator:
            try:
                got = _schema_match(stripped, top_k=3) or []
            except Exception:
                got = []
            got = [m for m in got if self._column_ok(dict(m))]
            if got:
                print(f"[SQLTool] 指标兜底：'{indicator}' 削后缀为 '{stripped}' 命中 "
                      f"{got[0].get('column_en')}")
                return got

        # 2) 整句同义词扫描
        if question:
            try:
                from field_matcher import match_from_question as _from_q
                hits = _from_q(question) or {}
            except Exception:
                hits = {}
            ranked = sorted(hits.items(),
                            key=lambda kv: -(kv[1].get("score") or 0))
            out = []
            for _key, info in ranked:
                col = info.get("column_en")
                if not col:
                    continue
                m = {
                    "column_en": col,
                    "table_name": info.get("table_name") or "",
                    "unit": info.get("unit") or "",
                    "score": info.get("score") or 0,
                    "display": info.get("display") or col,
                }
                if self._column_ok(m):
                    out.append(m)
                if len(out) >= 3:
                    break
            if out:
                print(f"[SQLTool] 指标兜底：'{indicator}' 经整句扫描命中 "
                      f"{out[0].get('column_en')}")
                return out

        # 3) 关键词表
        for kw, field in self.KEYWORD_TO_FIELD.items():
            if kw in (indicator or "") or kw in (question or ""):
                m = {"column_en": field, "table_name": "", "unit": "", "score": 0.5,
                     "display": field}
                if self._column_ok(m):
                    print(f"[SQLTool] 指标兜底：'{indicator}' 经关键词表命中 {field}")
                    return [m]

        return []

    # ========== 字段解析：统一调 field_matcher，不另起一套 ==========
    def _resolve_metric(self, metric_name):
        """
        通过 pipeline 的 field_matcher 解析字段名
        返回 (db_field, table) 或 (None, None)
        """
        if not metric_name:
            return None, None
        try:
            from field_matcher import match as field_match
            results = field_match(metric_name)
            if results and len(results) > 0:
                r = results[0]
                if r.get("score", 0) >= 0.8:
                    return r["column_en"], r["table_name"]
            return None, None
        except Exception as e:
            print(f"[SQLTool] field_matcher error: {e}")
            return None, None

    def _resolve_metric_with_fallback(self, metric_name, question=""):
        """
        Resolve field with explicit fallback. Handles both Chinese and English metric names.
        Returns (db_field, table, display_name, substituted)

        question: 原始问题文本；仅当直接匹配失败时用于兜底（N6 整句同义词扫描）。
        """
        # 1. Chinese name -> use field_matcher (single source of truth)
        is_chinese = any('\u4e00' <= c <= '\u9fff' for c in metric_name)
        if is_chinese:
            db_field, table = self._resolve_metric(metric_name)
        else:
            db_field, table = metric_name, None

        # 1b. N6 兜底：抽取层可能给出“指标 + 任务词”整串（如 '营收对比'），
        #     直配必然为空；依次尝试 削后缀 / 整句扫描 / 关键词表。
        if is_chinese and not db_field:
            for m in self._fallback_indicator_matches(metric_name, question):
                db_field = m.get("column_en")
                table = m.get("table_name") or None
                if db_field and (not table or self._column_exists(table, db_field)):
                    break
                db_field, table = None, None

        # 2. Check if column exists in DB
        if db_field:
            if table and self._column_exists(table, db_field):
                return db_field, table, metric_name, False
            for tbl in ["income_sheet", "balance_sheet", "cash_flow_sheet"]:
                if self._column_exists(tbl, db_field):
                    return db_field, tbl, metric_name, False

        # 3. Column not found -> try known fallbacks (explicitly tagged)
        fallback_map = {
            "total_profit": [("net_profit", "Net Profit")],
            "operating_profit": [("net_profit", "Net Profit")],
        }
        key = db_field or metric_name
        if key in fallback_map:
            for fb_field, fb_display in fallback_map[key]:
                for tbl in ["income_sheet"]:
                    if self._column_exists(tbl, fb_field):
                        return fb_field, tbl, fb_display, True

        return None, None, None, False

    async def execute(self, question: str = "", understanding: dict = None, query_type: str = "auto") -> str:
        """异步执行（ReAct 引擎调用）"""
        import json as _json
        result = self.run({"question": question, "understanding": understanding, "query_type": query_type})
        return _json.dumps(result, ensure_ascii=False, default=str)

    def run(self, inputs: dict) -> dict:
        question = inputs.get("question", "")
        understanding = inputs.get("understanding")
        query_type = inputs.get("query_type", "auto")

        if not question:
            return {"success": False, "error": "缺少question参数"}

        # ---- 自动补全 understanding ----
        # 当 LLM 直接传裸 question 时，内部跑 pipeline 提取指标
        # 这避免了 ReAct 循环中 sql_query → model2 → sql_query 的死循环
        try:
            if understanding is None or not understanding.get("indicators"):
                print(f"[SQLTool] understanding 缺失或空，自动补全...")
                try:
                    pipeline = self._get_pipeline()
                    understanding = pipeline.understand(question)
                    print(f"[SQLTool] pipeline 结果: intent={understanding.get('intent')}, "
                          f"indicators={understanding.get('indicators')}, "
                          f"companies={understanding.get('companies')}")
                except Exception as e:
                    print(f"[SQLTool] pipeline.understand() 失败: {e}，用关键词兜底")
                    understanding = self._keyword_understand(question)
        except Exception as e:
            print(f"[SQLTool] 自动补全失败: {e}")
            understanding = {"intent": "basic_query", "companies": [], "indicators": [], "years": [], "period": None}

        # 兜底：确保 understanding 不为 None
        if understanding is None:
            understanding = {"intent": "basic_query", "companies": [], "indicators": [], "years": [], "period": None}

        # ---- N5 修复：显式年份回填 ----
        understanding = self._backfill_years_from_question(understanding, question)

        return self._dispatch_query(inputs, question, understanding, query_type)

    @staticmethod
    def _backfill_years_from_question(understanding, question):
        """understanding.years 为空时，从问题文本回填显式年份（N5）。

        背景：实测问「云南白药2024年净利润是多少？」时 NER 漏抽年份
        （understanding.years=[]），导致生成的 SQL 不带 report_year 约束，
        命中 3 年 × 4 报告期 = 12 行，答案正确性完全依赖 LLM 自己从 12 行里
        挑出 2024 FY —— 属“脆弱正确”，行数一多就会挑错。

        问题文本里的 “20xx年” 是权威信号，缺则回填：
          - 仅在原 understanding **未给出**年份时生效，不覆盖已抽出的年份；
          - 支持多个年份（趋势类问题）；去重保序。
        """
        if not question or not isinstance(understanding, dict):
            return understanding
        try:
            existing = [int(y) for y in (understanding.get("years") or [])]
        except (TypeError, ValueError):
            existing = []
        if existing:
            return understanding
        found = [int(y) for y in re.findall(r"(20\d{2})", question)]
        if not found:
            return understanding
        seen, years = set(), []
        for y in found:
            if y not in seen:
                seen.add(y)
                years.append(y)
        understanding["years"] = years
        print(f"[SQLTool] years 缺失，从问题文本回填: {years}")
        return understanding

    def _keyword_understand(self, question: str) -> dict:
        """关键词兜底提取：当 pipeline 模型不可用时，从问题文本中提取指标/公司/年份"""
        import re
        from field_dict import SCHEMA_DICT

        # 公司名识别：唯一事实源（domain/normalize/company_names.py），最长优先、
        # 去重叠、按出现位置返回，天然支持「A 和 B 对比」的多公司。
        # 原先此处的"取前 2 字模糊匹配"启发式已删除：它会用「白云」「同仁」这类
        # 二字前缀乱命中，而别名表已覆盖常用省称，无需该启发式。
        companies = find_all_companies(question)

        # 指标关键词匹配：从 question 中找 SCHEMA_DICT 的中文同义词
        # 长词覆盖短词（同 field_matcher.match_from_question），
        # 避免"净资产收益率"里的"净资产"误命中 equity_total_equity 等子串歧义
        syn_pairs = []
        for en_name, info in SCHEMA_DICT.items():
            for syn in info.get("synonyms", []):
                if len(syn) >= 2:
                    syn_pairs.append((syn, en_name))
        syn_pairs.sort(key=lambda x: -len(x[0]))  # 长词优先
        indicators = []
        matched_syns = []  # 已匹配的 (同义词, 字段)，用于长词覆盖短词
        for syn, en_name in syn_pairs:
            if syn in question:
                covered = False
                for other_syn, _ in matched_syns:
                    if syn != other_syn and syn in other_syn:
                        covered = True
                        break
                if not covered:
                    matched_syns.append((syn, en_name))
                    indicators.append(en_name)

        # 去重（保留第一个匹配到的）
        seen = set()
        unique_indicators = []
        for ind in indicators:
            if ind not in seen:
                seen.add(ind)
                unique_indicators.append(ind)

        # 如果没有精确匹配，尝试用常见指标关键词
        # （表定义在类级 KEYWORD_TO_FIELD，与 _fallback_indicator_matches 共用，避免两套口径）
        if not unique_indicators:
            for kw, field in self.KEYWORD_TO_FIELD.items():
                if kw in question and field not in seen:
                    seen.add(field)
                    unique_indicators.append(field)

        # 年份
        years = []
        yr_match = re.findall(r"(20\d{2})", question)
        if yr_match:
            years = [int(y) for y in yr_match]

        # 报告期：委托 time_parser（词表唯一真相源 = time_parser.PERIOD_KEYWORDS）
        # 原先这里是一条 if-elif 关键词链，与 time_parser 的 PERIOD_KEYWORDS
        # 部分重叠、各自维护；且原链把 "Q2"/"二季度"/"中报"/"半年报" 一律判成
        # H1（把二季报当半年报），语义本身就是错的。
        period = None
        try:
            from time_parser import parse_time as _parse_time
            period = _parse_time(question).get("period")
        except Exception as e:
            print(f"[SQLTool] time_parser 不可用，报告期留空: {e}")

        # top_k：中文数词与「前 N」解析走唯一事实源（domain/normalize/query_slots.py），
        # 原先此处内联了一份 cn_num 数词表与正则，与 pipeline.py 的 _cn_num 并存。
        top_k = parse_top_k(question)

        # 意图判断（关键词**兜底**层：仅在 pipeline 模型不可用时走这里）
        intent = "basic_query"
        if top_k:
            intent = "stat_query"
        if self._looks_like_comparison(question, companies):
            intent = "comparison"
        if any(kw in question for kw in self.TREND_KEYWORDS):
            intent = "time_trend"

        result = {
            "intent": intent,
            "companies": companies,
            "indicators": unique_indicators,
            "years": years,
            "period": period,
            "top_k": top_k,
            "entities": {"COMP": companies, "METRIC": unique_indicators, "PERIOD": [period] if period else []},
            "needs_chart": False,
            "chart_type": None,
            "needs_rag": False,
        }
        print(f"[SQLTool] 关键词提取: intent={intent}, companies={companies}, "
              f"indicators={unique_indicators}, years={years}, period={period}, top_k={top_k}")
        return result

    def _dispatch_query(self, inputs, question, understanding, query_type):
        """查询调度：根据 query_type 分发到不同处理方法。

        N4 修复：专化分支（trend/compare）缺少其必需参数时**回退 auto**，
        而不是返回缺参错误。实测问「2024年营收排名前五的中药企业」时 LLM 首轮
        指定 query_type=compare，但该问题本就没有公司实体（抽取层已正确给出
        intent=stat_query / top_k=5 / years=[2024]，companies=[]），compare 分支
        直接回 missing_param、不执行 SQL，LLM 只能自己试第二轮 auto —— 排名类
        因此固定多烧一轮迭代（3 轮 vs 其余 2 轮）。回退 auto 后同轮即可作答。
        """
        try:
            if query_type == "trend":
                merged = self._merge_understanding(inputs, understanding)
                if merged.get("company") or merged.get("companies"):
                    return self._query_trend(merged)
                print("[SQLTool] trend 分支缺 company，回退 auto 路径")
                return self._query_with_understanding(question, understanding)
            elif query_type == "compare":
                merged = self._merge_understanding(inputs, understanding)
                if not merged.get("companies") and merged.get("company"):
                    merged["companies"] = [merged["company"]]
                if merged.get("companies"):
                    return self._query_compare(merged)
                print("[SQLTool] compare 分支缺 companies，回退 auto 路径")
                return self._query_with_understanding(question, understanding)
            else:
                return self._query_with_understanding(question, understanding)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _merge_understanding(self, inputs, understanding):
        """把自动补全的 understanding 合并进 trend / compare 所需参数。

        ReAct 路径下 LLM 通常只传 question + query_type，实体补全结果落在
        understanding 里；若不合并，_query_trend / _query_compare 会因缺
        company / companies / metric / years 直接返回错误，
        表现为模型反复重查同一工具直至迭代耗尽（实测 trend 路径 8/8 轮全废）。
        只填空缺或空值键，不覆盖调用方显式给出的有效值。
        """
        merged = dict(inputs)
        u = understanding or {}
        indicators = [i for i in (u.get("indicators") or []) if i]
        companies = [c for c in (u.get("companies") or []) if c]
        years = []
        for y in (u.get("years") or []):
            s = str(y).strip()
            if s.isdigit():
                years.append(int(s))

        def fill(key, value):
            if value and not merged.get(key):
                merged[key] = value

        fill("metric", indicators[0] if indicators else "")
        # 多指标对比（阶段0）：保留完整指标列表供 compare 分支逐指标查询。
        # 此前只填 metric=indicators[0]，其余指标被静默丢弃（答 1 漏 N-1）。
        if indicators and not merged.get("metrics"):
            merged["metrics"] = indicators
        if companies:
            fill("company", companies[0])
            if not merged.get("companies"):
                merged["companies"] = companies
        if years:
            fill("year_start", min(years))
            fill("year_end", max(years))
            fill("year", max(years))
        return merged

    def _query_with_understanding(self, question, understanding):
        """直接用 understanding 生成 SQL 并执行，不再重新跑 NER/Model2/time_parser"""
        if not understanding:
            return {"success": False, "error": "缺少 understanding"}

        from sql_generator import SQLGenerator
        from schema_mapping import match as schema_match

        intent = understanding.get("intent", "basic_query")
        companies = understanding.get("companies", [])
        indicators = understanding.get("indicators", [])
        years = understanding.get("years", [])
        period = understanding.get("period")
        top_k = understanding.get("top_k")

        # 构建 understanding dict 给 sql_generator
        gen_und = {
            "intent": intent,
            "company": companies[0] if companies else None,
            "companies": companies,
            "year": years[0] if years else None,
            "period": period,
            "top_k": top_k,
            "indicators": indicators,
            "is_multi_indicator": len(indicators) > 1,
            "is_multi_company": len(companies) > 1,
        }

        # 字段匹配（indicators 可能是中文指标名或英文字段名）
        all_matches = []
        seen_cols = set()
        print(f"[SQLTool] 开始匹配字段，indicators={indicators}")
        for ind in indicators:
            # 英文字段名直接查 SCHEMA_DICT，中文走 schema_match
            if all(ord(c) < 128 for c in ind):
                # KG Resolver 已处理的英文字段名，直接查列是否存在
                from field_dict import SCHEMA_DICT
                if ind in SCHEMA_DICT:
                    info = SCHEMA_DICT[ind]
                    table = info["table"]
                    col_exists = self._column_exists(table, ind)
                    print(f"[SQLTool] 英文字段 {ind} -> 表 {table}, 列存在: {col_exists}")
                    if col_exists:
                        matches = [{"column_en": ind, "table_name": table,
                                    "unit": info["unit"], "score": 1.0,
                                    "display": table + "." + ind + ": " + info["label"]}]
                    else:
                        matches = None
                else:
                    print(f"[SQLTool] 英文字段 {ind} 不在 SCHEMA_DICT 中")
                    matches = None
            else:
                matches = schema_match(ind, top_k=3)
                if not matches:
                    # N6 兜底：抽取层给出的可能是“指标 + 任务词”混合串
                    # （实测 indicators=['营收对比']），直配为空时走削后缀/整句扫描。
                    matches = self._fallback_indicator_matches(ind, question)
                print(f"[SQLTool] 中文字段 {ind} -> 匹配结果: {matches}")
            if matches:
                col_en = matches[0]["column_en"]
                if col_en not in seen_cols:
                    all_matches.append(matches[0])
                    seen_cols.add(col_en)

        print(f"[SQLTool] 最终匹配结果: {all_matches}")
        if not all_matches:
            return {"success": False, "error": "未匹配到字段", "data": []}

        # 生成 SQL
        sql_gen = SQLGenerator()
        sql = sql_gen.generate(gen_und, all_matches, question)
        if not sql or sql.startswith("--"):
            return {"success": False, "error": "SQL生成失败", "data": []}

        # ---- N1 修复：排名/对比类的报告期归并必须先于 LIMIT ----
        # 缺陷：`ORDER BY 指标 DESC LIMIT top_k` 作用在【未归并】的原始行上，
        # 同一公司的 FY/Q3/H1/Q1 多行会挤占名额。实测问“2024年营收排名前五”
        # 只给出 2 家（2024 年营收非空 273 行仅对应 70 家公司，前 5 行被
        # 白云山、云南白药的多期数据填满）。
        # 修法：把 SQL 的 LIMIT 放宽为候选集，执行后按公司归并（每公司保留
        # 覆盖度最高的报告期），再按主指标降序截断 top_k —— 语义即“每家公司
        # 只参与一次排名”。
        is_ranking = (top_k is not None) or intent in ("comparison", "stat_query")
        if is_ranking:
            sql = self._widen_limit(sql, top_k)

        # 执行（带重试）
        result_data, sql_str, retry_count, reason = self._execute_with_retry(
            sql, gen_und, sql_gen, all_matches, question, widen_limit=is_ranking)

        # 对比/排名查询：每家公司只保留优先级最高的报告期
        if result_data and intent in ("comparison", "stat_query"):
            result_data = self._dedup_by_company(result_data)
            # N1 修复（续）：归并后行序不再保证按指标降序，显式重排并按 top_k 截断
            if top_k and result_data:
                sort_col = all_matches[0].get("column_en") if all_matches else None
                if sort_col:
                    result_data.sort(key=lambda r: -self._as_float(r.get(sort_col)))
                try:
                    result_data = result_data[:max(int(top_k), 1)]
                except (TypeError, ValueError):
                    pass
            # 补充查了但没数据的公司（只有在用户明确指定了公司列表时才补充）
            # 对于排名查询（无明确公司列表），不要补充 NER 可能误识别的公司
            if companies and len(companies) > 0:
                # 判断是否是排名查询（无明确公司，只有 top_k）
                # 只有在用户明确指定了公司名时才补充，比如 "云南白药和片仔癀的营收对比"
                # 对于像 "营收排名前五" 这类查询，不补充
                is_ranking_query = (top_k is not None) or (not any(c in question for c in companies))
                if not is_ranking_query:
                    found = {r.get("stock_abbr") for r in result_data}
                    for comp in companies:
                        if comp not in found:
                            result_data.append({"stock_abbr": comp, "report_year": years[0] if years else "", "_no_data": True})

        # 格式化答案
        if result_data:
            answer = self._format_answer(intent, companies, indicators, years, period, top_k, result_data, all_matches)
        else:
            answer = ""

        # ---- 回写自动补全后的 understanding（B3 槽位回填）----
        # ReAct 引擎只传裸 question 时，本方法在 run() 内已完成
        # pipeline/keyword 自动补全并据此生成 SQL；把补全结果以顶层键
        # 回写进返回 JSON（只增键），供引擎侧解析 company/indicator/year
        # 槽位，避免真实链路指标槽位取不到。
        try:
            from agent.taxonomy import en_to_cn as _en_to_cn
        except Exception:
            _en_to_cn = lambda name: name
        # 以实际匹配/执行的字段为准（英文列名 → 中文标签），未匹配到时回退到
        # understanding 里已解析的指标词（中文词/概念组会原样保留）。
        resolved_labels = []
        for match in all_matches or []:
            col_en = match.get("column_en") if isinstance(match, dict) else ""
            if isinstance(col_en, str) and col_en.strip():
                label = _en_to_cn(col_en)
                if label and label not in resolved_labels:
                    resolved_labels.append(label)
        if not resolved_labels:
            for ind in understanding.get("indicators", []) or []:
                if ind and ind not in resolved_labels:
                    resolved_labels.append(ind)

        return {
            "success": bool(result_data),
            "data": result_data,
            "sql": sql_str,
            "answer": answer,
            "confidence": 0.85 if result_data else 0,
            "retry_count": retry_count,
            "reason": reason,
            "unit": "万元" if result_data else "",
            "companies": list(understanding.get("companies") or []),
            "indicators": resolved_labels,
            "years": list(understanding.get("years") or []),
        }

    def _execute_with_retry(self, sql, understanding, sql_gen, all_matches, question,
                            widen_limit=False):
        """4级降级重试：完整 → 去period → 去year → 全库

        widen_limit=True（排名/对比类）：降级重生成的 SQL 同样放宽 LIMIT。
        否则去重前候选行数不足，会重新触发 N1（同一公司多报告期挤占名额）。
        """
        import re
        retries = [
            (False, "完整参数"),
            (True, "去掉period"),
        ]

        for i, (strip_period, desc) in enumerate(retries):
            current_sql = sql

            if strip_period:
                current_sql = re.sub(r"\s*AND\s+report_period\s*=\s*'[^']*'", "", current_sql)
                current_sql = re.sub(r"\s*report_period\s*=\s*'[^']*'\s*AND\s*", " ", current_sql)
                current_sql = re.sub(r"WHERE\s+AND", "WHERE", current_sql)

            print(f"  [重试{i}] {desc}")
            print(f"  SQL: {current_sql[:200]}...")
            result_data = self._execute_sql(current_sql)
            if result_data:
                print(f"  查询到 {len(result_data)} 条数据")
                return result_data, current_sql, i, f"第{i+1}次尝试成功（{desc}）"
            else:
                print(f"  查询无数据")

        # 最后尝试：去掉year，取最新（仅当用户未明确指定年份时）
        original_year = understanding.get("year")
        # 用户问题中明确提到了年份（如"2023年"）-> 不降级year
        has_explicit_year = bool(__import__("re").search(r"(^|\D)(20\d{2})(\D|$)", question))
        if not original_year and not has_explicit_year:
            u = dict(understanding)
            u["year"] = None
            u["period"] = None
            sql3 = sql_gen.generate(u, all_matches, question)
            if sql3 and not sql3.startswith("--"):
                sql3 = re.sub(r"\s*AND\s+report_period\s*=\s*'[^']*'", "", sql3)
                sql3 = re.sub(r"\s*report_period\s*=\s*'[^']*'\s*AND\s*", " ", sql3)
                sql3 = re.sub(r"WHERE\s+AND", "WHERE", sql3)
                if widen_limit:
                    sql3 = self._widen_limit(sql3, understanding.get("top_k"))
                print(f"  [重试2] 去掉year，取最新")
                result_data = self._execute_sql(sql3)
                if result_data:
                    return result_data, sql3, 2, "第3次尝试成功（去掉year）"

        return None, sql, len(retries), "所有重试均无数据"

    # 报告期优先级统一由 agent.tools.base.PERIOD_PRIORITY 提供（模块级单一来源）。
    # 历史上此处曾另定义 {"FY":4,"H1":3,"Q3":2,"Q1":1}，与模块级 FY>Q3>H1>Q1 冲突，
    # 已删除以免两套口径并存。

    def _is_row_valid(self, row):
        """检查一行数据的比率字段是否在合理范围内（排除明显错误的提取结果）"""
        for key, val in row.items():
            if val is None:
                continue
            if key in ("stock_abbr", "report_year", "report_period", "stock_code", "rank", "note",
                       "_tbl_priority", "_rn", "_no_data"):
                continue
            try:
                v = float(val)
                # 比率字段：毛利率/净利率/ROE 合理范围 -100% ~ 100%
                if any(kw in key for kw in ("margin", "ratio", "roe", "growth")):
                    if v > 1000 or v < -1000:
                        return False
            except (ValueError, TypeError):
                pass
        return True

    def _count_valid_values(self, row):
        """统计行中非空且合理的值数量"""
        count = 0
        for key, val in row.items():
            if val is None:
                continue
            if key in ("stock_abbr", "report_year", "report_period", "stock_code", "rank", "note",
                       "_tbl_priority", "_rn", "_no_data"):
                continue
            try:
                float(val)
                count += 1
            except (ValueError, TypeError):
                pass
        return count

    def _dedup_by_company(self, data):
        """对比/排名查询：每家公司只保留优先级最高的有效报告期"""
        best = {}
        for row in data:
            name = row.get("stock_abbr", "")
            rp = row.get("report_period", "")
            pri = period_priority(rp)
            row_valid = self._is_row_valid(row)

            if name not in best:
                best[name] = (row, pri, row_valid)
            else:
                old_row, old_pri, old_valid = best[name]
                # 优先选择有效数据
                if row_valid and not old_valid:
                    best[name] = (row, pri, row_valid)
                elif row_valid == old_valid:
                    if pri > old_pri:
                        best[name] = (row, pri, row_valid)
                    elif pri == old_pri:
                        old_nulls = self._count_valid_values(old_row)
                        new_nulls = self._count_valid_values(row)
                        if new_nulls > old_nulls:
                            best[name] = (row, pri, row_valid)
        return [v[0] for v in best.values()]

    # 排名类放宽后的候选行上限（去重前的原始行数）
    RANK_CANDIDATE_LIMIT = 200

    @staticmethod
    def _as_float(val):
        """安全转 float；None/不可转返回 -inf（降序排序时自然落到末尾）。"""
        try:
            return float(val)
        except (TypeError, ValueError):
            return float("-inf")

    def _widen_limit(self, sql, top_k=None):
        """把 SQL 尾部的 LIMIT 放宽为候选集，供“按公司去重后再截断”使用（N1 修复）。

        排名/对比类若沿用 `LIMIT top_k`，同一公司的多个报告期会挤占名额；
        放宽后可取到足够候选行，再由 _dedup_by_company 归并为每公司一行。
        无 LIMIT 时不追加，避免改变“取全部”的原语义。
        """
        import re as _re
        if not sql:
            return sql
        m = _re.search(r"\bLIMIT\s+(\d+)\s*(;?)\s*$", sql, flags=_re.I)
        if not m:
            return sql
        current = int(m.group(1))
        try:
            widened = max(int(top_k) * 20, self.RANK_CANDIDATE_LIMIT) if top_k else self.RANK_CANDIDATE_LIMIT
        except (TypeError, ValueError):
            widened = self.RANK_CANDIDATE_LIMIT
        widened = max(widened, current)
        if widened == current:
            return sql
        return sql[:m.start()] + "LIMIT %d%s" % (widened, m.group(2))

    def _format_answer(self, intent, companies, indicators, years, period, top_k, data, matches):
        """格式化答案文本"""
        if not data:
            return "未查询到相关数据。"

        # 指标中文名
        indicator_cn = indicators[0] if indicators else ""
        if matches and matches[0].get("display"):
            disp = matches[0]["display"]
            if ": " in disp:
                indicator_cn = disp.split(": ", 1)[1]
            elif "：" in disp:
                indicator_cn = disp.split("：", 1)[1]

        value_cols = [k for k in data[0].keys()
                      if k not in ("stock_abbr", "report_year", "report_period", "stock_code", "rank", "note",
                                   "_tbl_priority", "_rn")]

        def fmt_num(num):
            if num is None:
                return "0"
            try:
                n = float(num)
                if abs(n) >= 10000:
                    return "{:.2f}".format(n)
                elif abs(n) >= 1:
                    return "{:.2f}".format(n)
                else:
                    return "{:.4f}".format(n)
            except:
                return str(num)

        if intent == "basic_query":
            name = data[0].get("stock_abbr", companies[0] if companies else "")
            y = data[0].get("report_year", years[0] if years else "")
            if len(data) == 1:
                val = data[0].get(value_cols[0], 0) if value_cols else 0
                rp = data[0].get("report_period", "")
                pstr = "（" + rp + "）" if rp and rp != "FY" else ""
                return f"{name}{y}年{pstr}的{indicator_cn}是 {fmt_num(val)} 万元。"
            else:
                # N5 修复（续）：多行答案在跨年份时必须逐行标出“年份+报告期”，
                # 否则同一公司多年多期的行会输出成 12 行无法区分的流水账
                # （实测「云南白药2024年净利润是多少？」修复前正是如此）。
                multi_year = len({str(r.get("report_year", "")) for r in data}) > 1
                header = f"{name}{indicator_cn}：" if multi_year else f"{name}{y}年{indicator_cn}："
                lines = [header]
                for row in data:
                    val = row.get(value_cols[0], 0) if value_cols else 0
                    rp = row.get("report_period", "")
                    ry = row.get("report_year", "")
                    note = row.get("note", "")
                    label = f"{ry}年{rp}" if multi_year else (rp if rp else "未知")
                    line = f"  {label}：{fmt_num(val)} 万元"
                    if note:
                        line += f"（{note}）"
                    lines.append(line)
                return "\n".join(lines)

        # time_trend placeholder
        pass
# stat_query / comparison
        # 从 matches 获取指标中文名
        metric_names = []
        for m in (matches or []):
            disp = m.get("display", "")
            cn = disp.split(": ", 1)[1] if ": " in disp else (disp.split("：", 1)[1] if "：" in disp else m.get("column_en", ""))
            metric_names.append(cn)
        if not metric_names:
            # 原 self.METRIC_EN_TO_CN 定义在已删除的 agent/agent.py，现按收敛
            # 方向改走 agent.taxonomy 单一英文列名->中文标签映射（未匹配原样保留）
            try:
                from agent.taxonomy import en_to_cn as _metric_en_to_cn
            except Exception:
                _metric_en_to_cn = lambda vc: vc
            metric_names = [_metric_en_to_cn(vc) for vc in value_cols]

        lines = []
        if top_k:
            title = "、".join(metric_names) if len(metric_names) > 1 else indicator_cn
            lines.append(f"{title}排名前{top_k}的企业：")
        else:
            title = "、".join(metric_names) if len(metric_names) > 1 else indicator_cn
            lines.append(f"{title}查询结果：")

        for i, row in enumerate(data[:10], 1):
            name = row.get("stock_abbr", "未知")
            y = row.get("report_year", "")
            rp = row.get("report_period", "")
            note = row.get("note", "")
            pstr = f" {rp}" if rp and rp != "FY" else ""
            nstr = f"（{note}）" if note else ""

            # 查了但没数据的公司
            if row.get("_no_data"):
                if y:
                    lines.append(f"  {i}. {name}（{y}年{pstr}）：暂无数据")
                else:
                    lines.append(f"  {i}. {name}：暂无数据")
                continue

            if len(value_cols) <= 1:
                # 单指标
                val = row.get(value_cols[0], 0) if value_cols else 0
                unit = "万元" if "margin" not in (value_cols[0] if value_cols else "") and "ratio" not in (value_cols[0] if value_cols else "") and "roe" not in (value_cols[0] if value_cols else "") else "%"
                if y:
                    lines.append(f"  {i}. {name}（{y}年{pstr}）：{fmt_num(val)} {unit}{nstr}")
                else:
                    lines.append(f"  {i}. {name}：{fmt_num(val)} {unit}{nstr}")
            else:
                # 多指标：每个指标一行
                if y:
                    lines.append(f"  {i}. {name}（{y}年{pstr}）：")
                else:
                    lines.append(f"  {i}. {name}：")
                for vc, mn in zip(value_cols, metric_names):
                    val = row.get(vc)
                    if val is not None:
                        unit = "%" if any(kw in vc for kw in ("margin", "ratio", "roe", "growth")) else "万元"
                        lines.append(f"     {mn}：{fmt_num(val)} {unit}")
                if note:
                    lines.append(f"     备注：{note}")
        return "\n".join(lines)

    def _query_trend(self, inputs):
        """
        趋势查询：每年只返回一条，优先线 FY > Q3 > H1 > Q1
        字段解析统一走 field_matcher，回退时显式标记
        """
        raw_metric = inputs.get("metric", "net_profit")
        company = inputs.get("company", "")
        year_start = inputs.get("year_start", 2022)
        year_end = inputs.get("year_end", 2026)

        if not company:
            return {"success": False, "error": "缺少company参数"}

        # 优先用 field_matcher 解析字段名
        # 如果 raw_metric 已经是英文名（如 "net_profit"），直接尝试
        actual_metric, table, display_name, substituted = self._resolve_metric_with_fallback(
            raw_metric, inputs.get("question", ""))

        if not actual_metric or not table:
            # field_matcher 也没解析到，用原始值尝试所有表兜底
            actual_metric = raw_metric
            for tbl in ["income_sheet", "balance_sheet", "cash_flow_sheet"]:
                if self._column_exists(tbl, actual_metric):
                    table = tbl
                    break

        if not table or not self._column_exists(table, actual_metric):
            # 在所有表中都找不到该列
            return {
                "success": False,
                "data": [],
                "error": f"数据库中不存在字段「{raw_metric}」，且无合适替代字段",
                "reason": "field_not_found",
                "metric": raw_metric,
            }

        conn = self._get_conn()
        c = conn.cursor(dictionary=True)

        all_rows = []
        sql_trace = ""
        try:
            sql = "SELECT stock_abbr, report_year, report_period, %s FROM %s WHERE stock_abbr LIKE %%s AND report_year BETWEEN %%s AND %%s AND %s IS NOT NULL" % (actual_metric, table, actual_metric)
            params = ("%" + company + "%", year_start, year_end)
            c.execute(sql, params)
            # 2026-09-17：SQL 溯源需要。此前 _query_trend / _query_compare 建了
            # 语句却不回传，只有 _query_with_understanding 回传 → 前端「SQL 溯源」
            # 在趋势 / 对比类问题上是空的（实测 f_rag.json 的 sql 长度为 0）。
            # 纯增量：只多返回一个展示用字符串，不改任何查询行为。
            sql_trace = sql + "   -- 参数: " + ", ".join(repr(p) for p in params)
            all_rows.extend(c.fetchall())
        except Exception as e:
            c.close()
            return {"success": False, "data": [], "error": str(e), "reason": "query_error", "metric": actual_metric}

        c.close()

        if not all_rows:
            # 尝试用 pipeline 兜底
            try:
                pipeline = self._get_pipeline()
                result = pipeline.query(inputs.get("question", ""))
                if result.get("success") and result.get("data"):
                    return {
                        "success": True,
                        "data": result["data"],
                        "query_type": "trend",
                        "company": company,
                        "metric": actual_metric,
                        "note": "使用pipeline兜底查询",
                        "substituted": substituted,
                        "original_metric": raw_metric if substituted else None,
                        "display_metric": display_name if substituted else None,
                    }
            except:
                pass
            return {"success": False, "data": [], "error": f"未找到 {company} 在 {year_start}-{year_end} 年的数据", "reason": "no_data"}

        # 每年只保留优先级最高的报告期
        best_per_year = {}
        for row in all_rows:
            year = row["report_year"]
            period = row.get("report_period", "")
            priority = PERIOD_PRIORITY.get(period, 0)
            if year not in best_per_year or priority > PERIOD_PRIORITY.get(best_per_year[year].get("report_period", ""), 0):
                best_per_year[year] = row

        result = sorted(best_per_year.values(), key=lambda x: x["report_year"])

        for row in result:
            if row.get("report_period") != "FY":
                row["note"] = "注：%d年无年报，使用%s数据" % (row["report_year"], row.get("report_period", ""))

        return {
            "success": bool(result),
            "data": result,
            "sql": sql_trace,
            "query_type": "trend",
            "company": company,
            "metric": actual_metric,
            "substituted": substituted,
            "original_metric": raw_metric if substituted else None,
            "display_metric": display_name if substituted else None,
        }

    def _query_compare(self, inputs):
        """对比查询：每家公司独立查，各自降级"""
        # 阶段0：多指标对比分流到专用路径。此前只查 metric=indicators[0]，
        # 「营业收入和净利润」只回 1 列却 success=True —— 静默漏答。
        raw_metrics = inputs.get("metrics")
        if isinstance(raw_metrics, (list, tuple)) and len(raw_metrics) > 1:
            return self._query_compare_multi(inputs, list(raw_metrics))
        raw_metric = inputs.get("metric", "total_operating_revenue")
        companies = inputs.get("companies", [])
        year = inputs.get("year", 2024)

        if not companies:
            return {"success": False, "error": "缺少companies参数"}

        # 解析字段
        actual_metric, table, display_name, substituted = self._resolve_metric_with_fallback(
            raw_metric, inputs.get("question", ""))
        if not actual_metric or not table:
            actual_metric = raw_metric
            for tbl in ["income_sheet", "balance_sheet", "cash_flow_sheet"]:
                if self._column_exists(tbl, actual_metric):
                    table = tbl
                    break

        if not table:
            return {"success": False, "data": [], "error": f"数据库中不存在字段「{raw_metric}」", "reason": "field_not_found"}

        conn = self._get_conn()
        c = conn.cursor(dictionary=True)

        results = []
        sql_lines = []          # 2026-09-17：SQL 溯源用（见 _query_trend 同处注释）
        for company in companies:
            row = None
            used_period = None

            for period in PERIOD_FALLBACK:
                sql = "SELECT stock_abbr, report_year, report_period, %s FROM %s WHERE stock_abbr LIKE %%s AND report_year = %%s AND report_period = %%s AND %s IS NOT NULL LIMIT 1" % (actual_metric, table, actual_metric)
                params = ("%" + company + "%", year, period)
                c.execute(sql, params)
                line = sql + "   -- 参数: " + ", ".join(repr(p) for p in params)
                if line not in sql_lines:
                    sql_lines.append(line)
                row = c.fetchone()
                if row:
                    used_period = period
                    break

            if row:
                if used_period != "FY":
                    row["note"] = "注：%d年无年报，使用%s数据" % (year, used_period)
                results.append(row)
            else:
                results.append({
                    "stock_abbr": company,
                    "report_year": year,
                    actual_metric: None,
                    "note": "注：%d年无数据" % year,
                })

        c.close()

        return {
            "success": bool(results),
            "data": results,
            "sql": "\n".join(sql_lines),
            "query_type": "compare",
            "companies": companies,
            "metric": actual_metric,
            "substituted": substituted,
            "original_metric": raw_metric if substituted else None,
            "display_metric": display_name if substituted else None,
        }

    def _query_compare_multi(self, inputs, raw_metrics):
        """多指标对比（阶段0新增）：每家公司一行、每指标一列。

        每个指标独立查询、各自按 PERIOD_FALLBACK 降级（不同指标允许
        落到不同报告期，逐指标记 note）；查不到的字段跳过（记日志），
        全部失败才 field_not_found —— 让"哪个指标没答"在数据里可见，
        而不是静默丢列。
        """
        companies = inputs.get("companies", [])
        year = inputs.get("year", 2024)
        question = inputs.get("question", "")

        # 逐指标解析字段（各自走替换/降级）
        resolved = []
        for raw_metric in raw_metrics:
            actual_metric, table, display_name, substituted = \
                self._resolve_metric_with_fallback(raw_metric, question)
            if not actual_metric or not table:
                actual_metric = raw_metric
                for tbl in ["income_sheet", "balance_sheet", "cash_flow_sheet"]:
                    if self._column_exists(tbl, actual_metric):
                        table = tbl
                        break
            if table:
                if not any(r[0] == actual_metric for r in resolved):
                    resolved.append((actual_metric, table, display_name,
                                     substituted, raw_metric))
            else:
                print(f"[SQLTool] compare 多指标：字段「{raw_metric}」不存在，跳过")

        if not resolved:
            return {"success": False, "data": [],
                    "error": f"数据库中不存在字段「{'、'.join(raw_metrics)}」",
                    "reason": "field_not_found"}

        conn = self._get_conn()
        c = conn.cursor(dictionary=True)

        results = []
        for company in companies:
            row = {"stock_abbr": company, "report_year": year}
            periods_used = set()
            notes = []
            for actual_metric, table, display_name, substituted, raw_metric in resolved:
                mrow = None
                used_period = None
                for period in PERIOD_FALLBACK:
                    sql = ("SELECT stock_abbr, report_year, report_period, %s FROM %s "
                           "WHERE stock_abbr LIKE %%s AND report_year = %%s "
                           "AND report_period = %%s AND %s IS NOT NULL LIMIT 1"
                           % (actual_metric, table, actual_metric))
                    c.execute(sql, ("%" + company + "%", year, period))
                    mrow = c.fetchone()
                    if mrow:
                        used_period = period
                        break
                if mrow:
                    row[actual_metric] = mrow.get(actual_metric)
                    periods_used.add(used_period)
                    if used_period != "FY":
                        notes.append("%s：%d年无年报，使用%s数据"
                                     % (display_name, year, used_period))
                else:
                    row[actual_metric] = None
                    notes.append("%s：%d年无数据" % (display_name, year))
            if len(periods_used) == 1:
                row["report_period"] = periods_used.pop()
            if notes:
                row["note"] = "注：" + "；".join(notes)
            results.append(row)
        c.close()

        return {
            "success": bool(results),
            "data": results,
            "query_type": "compare",
            "companies": companies,
            "metric": resolved[0][0],
            "metrics": [r[0] for r in resolved],
            "substituted": any(r[3] for r in resolved),
        }
