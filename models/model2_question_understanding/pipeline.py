# -*- coding: utf-8 -*-
"""金融问答Pipeline：NER + Model2 + time_parser + schema_match + SQL + 重试"""
import json, sys, os, re, mysql.connector, traceback
sys.path.insert(0, os.path.dirname(__file__))
from inference_model2 import QuestionUnderstandingModel
from schema_mapping import match as schema_match
from sql_generator import SQLGenerator, FIELD_TABLES
from ner_inference import NERExtractor
from time_parser import parse_time as parse_question_time

# ===== 公司名字典 =====
COMPANY_NAMES = ["白云山", "云南白药", "华润三九", "同仁堂", "太极集团", "步长制药", "以岭药业",
    "片仔癀", "济川药业", "天士力", "达仁堂", "瑞康医药", "昆药集团", "康恩贝", "信邦制药",
    "红日药业", "葵花药业", "仁和药业", "康美药业", "康缘药业", "东阿阿胶", "江中药业",
    "健民集团", "康弘药业", "千金药业", "振东制药", "羚锐制药", "新里程", "珍宝岛",
    "中恒集团", "马应龙", "亚宝药业", "九芝堂", "益佰制药", "众生药业", "金花股份",
    "神奇制药", "桂林三金", "太龙药业", "奇正藏药", "佐力药业", "精华制药", "万邦德",
    "通化金马", "康泰医学", "盘龙药业", "华神科技", "新天药业", "恩威医药", "华森制药",
    "沃华医药", "寿仙谷", "特一药业", "益盛药业", "康惠制药", "嘉应制药", "维康药业",
    "启迪药业", "粤万年青", "新光药业", "天目药业", "莱茵生物", "广誉远", "贵州三力",
    "方盛制药", "陇神戎发", "999", "白药", "金花", "花"]
COMPANY_ALIAS = {"999": "华润三九", "三金": "桂林三金", "白云": "白云山", "同仁": "同仁堂",
    "云白": "云南白药", "花": "葵花药业"}


def extract_company(q):
    """从问题中提取公司名（字典匹配）"""
    for n in COMPANY_NAMES:
        if n in q:
            return COMPANY_ALIAS.get(n, n)
    return None


class FinancialQAPipeline:
    def __init__(self):
        print("=" * 60)
        print("金融问答Pipeline初始化")
        print("=" * 60)
        print("\n[1/4] 加载 Model 2 (意图分类)...")
        self.model2 = QuestionUnderstandingModel()
        print("\n[2/4] 加载 NER 模型 (槽位抽取)...")
        self.ner = NERExtractor()
        print("\n[3/4] 加载字段映射词典...")
        from field_dict import SCHEMA_DICT
        print("  词典已加载，共 " + str(len(SCHEMA_DICT)) + " 个字段")
        print("\n[4/4] 连接数据库...")
        self.db_config = {"host": "localhost", "port": 3306, "database": "finance_data", "user": "root", "password": "433127hj"}
        self.db_conn = None
        self.sql_gen = SQLGenerator()
        self._connect_db()
        print("\n初始化完成!")

    def _connect_db(self):
        try:
            self.db_conn = mysql.connector.connect(**self.db_config)
            print("  数据库连接成功!")
        except Exception as e:
            print("  数据库连接失败: " + str(e))

    def query(self, question, is_multi_turn=False):
        print("\n" + "=" * 60)
        print("问题: " + question)
        print("=" * 60)

        # ===== Step 1: NER抽取 =====
        print("\n[Step 1] NER 抽取槽位...")
        ner_slots = self.ner.extract_slots(question) if self.ner.model else {}
        print("  NER槽位: " + json.dumps(ner_slots, ensure_ascii=False))

        # ===== Step 2: Model2意图 =====
        print("\n[Step 2] Model 2 识别意图...")
        understanding = self.model2.understand(question, is_multi_turn)
        intent = understanding.get("intent", "basic_query")
        print("  意图: " + intent)

        if intent == "open_question":
            return self._make_result(False, question=question, answer="开放性问题，无需查库")

        # ===== Step 3: 合并槽位 =====
        companies = ner_slots.get("COMP", [])
        indicators = ner_slots.get("METRIC", [])
        periods = ner_slots.get("PERIOD", [])

        company = companies[0] if companies else understanding.get("company")
        if not indicators and understanding.get("indicator"):
            indicators = [understanding["indicator"]]
        period = periods[0] if periods else understanding.get("period")

        # 多轮对话：company/indicator为空时，从Model2的context继承
        if is_multi_turn:
            ctx = self.model2.context
            if not company and ctx.get("company"):
                company = ctx["company"]
                print(f"  [多轮] 继承公司: {company}")
            if not indicators and ctx.get("indicator"):
                indicators = [ctx["indicator"]]
                print(f"  [多轮] 继承指标: {indicators}")

        # year/period: time_parser正则提取，覆盖Model2
        time_info = parse_question_time(question)
        year = time_info.get("year") or understanding.get("year")
        period = time_info.get("period") or period
        print(f"  time_parser: year={year}, period={period}")

        # 公司兜底：字典匹配
        if not company:
            company = extract_company(question)
            if company:
                print(f"  [Fix] 公司字典兜底: {company}")

        # 更新understanding
        understanding["company"] = company
        understanding["indicator"] = indicators[0] if indicators else ""
        understanding["indicators"] = indicators
        understanding["companies"] = companies
        understanding["period"] = period
        understanding["year"] = year

        # 意图修正：comparison但只有一个公司 → basic_query
        if intent == "comparison" and company and len(companies) < 2:
            intent = "basic_query"
            understanding["intent"] = intent
            print(f"  [Fix] comparison -> basic_query (单公司)")

        # 基本校验
        if intent == "basic_query":
            if not company:
                return self._make_result(False, question=question, answer="无法识别公司", reason="公司名未识别")
            if not year:
                return self._make_result(False, question=question, answer="无法识别时间", reason="年份未识别")
        if not indicators:
            return self._make_result(False, question=question, answer="未识别到财务指标", reason="指标未识别")

        # ===== Step 4: 字段匹配 =====
        print("\n[Step 4] 字段匹配...")
        all_matches = []
        seen_cols = set()
        for ind in indicators:
            matches = schema_match(ind, top_k=3)
            if matches:
                col_en = matches[0]["column_en"]
                if col_en not in seen_cols:  # 去重
                    all_matches.append(matches[0])
                    seen_cols.add(col_en)
                    print('  "' + ind + '" -> ' + matches[0]["display"] + " (score: " + str(matches[0]["score"]) + ")")
                else:
                    print('  "' + ind + '" -> ' + matches[0]["display"] + " (重复，跳过)")
            else:
                print('  "' + ind + '" -> 未匹配')
        if not all_matches:
            return self._make_result(False, question=question, answer="未匹配到数据库字段", reason="字段匹配失败")

        # ===== Step 4.5: 检测是否为排名交集查询 =====
        from compare import rank as cmp_rank, intersect_rank
        import re
        result_data = None
        sql_str = ""
        retry_count = 0
        reason = ""
        _cn_num = {'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}
        intersection_match = re.search(r'(?:均|都|同时).*排名.*?前(\d+|[一二三四五六七八九十]+)', question)
        top_n_match = re.search(r'前(\d+|[一二三四五六七八九十]+)', question)
        if top_n_match:
            raw = top_n_match.group(1)
            top_n = int(raw) if raw.isdigit() else _cn_num.get(raw, None)
        else:
            top_n = None

        if intersection_match and len(all_matches) >= 2 and top_n:
            # 排名交集查询：查所有数据，分别排名，取交集
            print("\n[Step 4.5] 排名交集查询，top=%d" % top_n)
            # 去掉top_k限制，查所有数据
            u_all = dict(understanding)
            u_all["top_k"] = None
            u_all["period"] = None  # 查所有期
            all_data, _, _, _ = self._query_with_retry(u_all, all_matches, question)
            if all_data:
                # 对每个指标分别排名
                rankings = {}
                for m in all_matches:
                    col = m["column_en"]
                    ranked = cmp_rank(all_data, col, top_k=top_n)
                    rankings[col] = ranked
                    print("  %s TOP%d: %s" % (col, top_n, [r.get("stock_abbr") for r in ranked]))
                # 取交集
                if len(rankings) >= 2:
                    cols_list = list(rankings.values())
                    result_data = cols_list[0]
                    for other in cols_list[1:]:
                        result_data = intersect_rank(result_data, other, top_k=top_n)
                    sql_str = "intersection query"
                    reason = "排名交集: %s" % " ∩ ".join(rankings.keys())
                    retry_count = 0
                    print("  交集结果: %d条" % len(result_data))

        if not result_data:
            # ===== Step 5: 生成SQL + 执行 + 重试 =====
            print("\n[Step 5] 生成SQL + 执行...")
            result_data, sql_str, retry_count, reason = self._query_with_retry(understanding, all_matches, question)

        # ===== Step 6: 生成答案 =====
        print("\n[Step 6] 生成答案...")
        answer = self._format_answer(understanding, all_matches, result_data)
        print("  " + answer)

        # 计算置信度
        score = 0.3
        if intent in {"basic_query", "stat_query", "comparison", "time_trend", "fuzzy_intent", "analysis_query"}:
            score += 0.25
        if indicators: score += 0.2
        if company: score += 0.15
        if year: score += 0.1
        if period: score += 0.05
        confidence = round(min(0.95, max(0.1, score)), 2)

        success = bool(result_data)
        return self._make_result(
            success, question=question, data=result_data, sql=sql_str, answer=answer,
            confidence=confidence, retry_count=retry_count, reason=reason,
            slots={"company": company, "year": year, "indicator": understanding.get("indicator", ""), "period": period}
        )

    def _query_with_retry(self, understanding, all_matches, question):
        """4级降级重试：FY → 去period → 去year → 全库"""
        # 重试配置：(overrides, 是否去掉period过滤, 描述)
        retries = [
            ({}, False, "完整参数"),
            ({"period": None}, True, "去掉period"),
            ({"period": None, "year": None}, True, "去掉year，取最新"),
        ]

        for i, (overrides, strip_period, desc) in enumerate(retries):
            u = dict(understanding)
            for k, v in overrides.items():
                u[k] = v

            sql = self.sql_gen.generate(u, all_matches, question)
            if not sql or sql.startswith("--"):
                print(f"  [重试{i}] {desc}: SQL生成失败")
                continue

            # 去掉period过滤：SQL generator会自动加FY，需要手动去除
            if strip_period:
                sql = re.sub(r"\s*AND\s+report_period\s*=\s*'[^']*'", "", sql)
                sql = re.sub(r"\s*report_period\s*=\s*'[^']*'\s*AND\s*", " ", sql)
                sql = re.sub(r"WHERE\s+AND", "WHERE", sql)

            print(f"  [重试{i}] {desc}")
            print(f"  SQL: {sql[:200]}...")
            result_data = self._execute_sql(sql)
            if result_data:
                print(f"  查询到 {len(result_data)} 条数据")
                return result_data, sql, i, f"第{i+1}次尝试成功（{desc}）"
            else:
                print(f"  查询无数据")

        return None, "", len(retries), "所有重试均无数据"

    def _execute_sql(self, sql):
        if not self.db_conn or not sql or sql.startswith("--"):
            return None
        try:
            # 重连检测
            try:
                self.db_conn.ping(reconnect=True)
            except:
                self._connect_db()
            cursor = self.db_conn.cursor(dictionary=True)
            cursor.execute(sql)
            results = cursor.fetchall()
            cursor.close()
            return results
        except Exception as e:
            print("  SQL执行错误: " + str(e))
            return None

    def _make_result(self, success, **kwargs):
        """统一返回格式"""
        result = {
            "success": success,
            "data": kwargs.get("data"),
            "sql": kwargs.get("sql"),
            "answer": kwargs.get("answer", ""),
            "confidence": kwargs.get("confidence", 0.5),
            "retry_count": kwargs.get("retry_count", 0),
            "reason": kwargs.get("reason", ""),
            "slots": kwargs.get("slots", {}),
            "question": kwargs.get("question", ""),
        }
        return result

    def _format_answer(self, understanding, matches, data):
        intent = understanding.get("intent", "basic_query")
        company = understanding.get("company", "")
        indicator = understanding.get("indicator", "")
        year = understanding.get("year", "")
        top_k = understanding.get("top_k")
        is_multi = understanding.get("is_multi_indicator", False)
        if not data:
            return "未查询到相关数据。"
        value_cols = [k for k in data[0].keys() if k not in ("stock_abbr", "report_year", "report_period", "stock_code")]
        if is_multi or len(value_cols) > 1:
            lines = []
            if top_k:
                lines.append("排名前" + str(top_k) + "的企业：")
            for i, row in enumerate(data[:10], 1):
                parts = [str(i) + ". " + row.get("stock_abbr", "未知")]
                y = row.get("report_year", "")
                if y: parts.append(str(y) + "年")
                for vc in value_cols:
                    val = row.get(vc, "")
                    if val is not None and val != "":
                        parts.append(self._format_num(val) + "万元")
                lines.append("  ".join(parts))
            return "\n".join(lines)
        if intent == "basic_query" and data:
            row = data[0]
            val = row.get(value_cols[0], 0) if value_cols else 0
            name = row.get("stock_abbr", company)
            y = row.get("report_year", year)
            return name + str(y) + "年的" + indicator + "是 " + self._format_num(val) + " 万元。"
        if intent in ("stat_query", "fuzzy_intent", "comparison", "time_trend", "analysis_query") and data:
            lines = []
            if top_k:
                lines.append(indicator + "排名前" + str(top_k) + "的企业：")
            else:
                lines.append(indicator + "查询结果：")
            for i, row in enumerate(data[:10], 1):
                name = row.get("stock_abbr", "未知")
                val = row.get(value_cols[0], 0) if value_cols else 0
                y = row.get("report_year", "")
                if y:
                    lines.append("  " + str(i) + ". " + name + "(" + str(y) + "年): " + self._format_num(val) + "万元")
                else:
                    lines.append("  " + str(i) + ". " + name + ": " + self._format_num(val) + "万元")
            return "\n".join(lines)
        return "查询到 " + str(len(data)) + " 条数据。"

    def _format_num(self, num):
        if num is None: return "0"
        try:
            n = float(num)
            if n >= 10000: return "{:.2f}".format(n)
            elif n >= 1: return "{:.2f}".format(n)
            else: return "{:.4f}".format(n)
        except: return str(num)

    def multi_turn(self, question):
        return self.query(question, is_multi_turn=True)

    def reset(self):
        self.model2.reset_context()


if __name__ == "__main__":
    pipeline = FinancialQAPipeline()
    print("\n问答助手已启动")
    while True:
        try:
            q = input("\n>>> ").strip()
            if q.lower() in ("exit", "quit", "退出"): break
            if q.lower() in ("reset", "重置"): pipeline.reset(); print("已重置"); continue
            if not q: continue
            result = pipeline.query(q)
            if result.get("answer"): print("\n" + result["answer"])
        except KeyboardInterrupt: break
        except Exception as e: print("出错: " + str(e)); traceback.print_exc()
