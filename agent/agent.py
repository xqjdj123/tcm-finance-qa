# -*- coding: utf-8 -*-
"""Finance Agent: TaskType分类 → Planner规划 → Executor执行 → LLM总结"""
import sys, os, json, re
from datetime import datetime
from enum import Enum

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.config import MAX_STEPS
from agent.llm import LLMClient
from agent.session import get_session_manager
from agent.tools.sql_tool import SQLTool
from agent.tools.rag_tool import RAGTool
from agent.tools.data_tool import DataTool
from agent.tools.chart_tool import ChartTool
from agent.tools.risk_tool import RiskTool
from agent.tools.report_tool import ReportTool


class TaskType(Enum):
    QUERY = "query"
    ANALYSIS = "analysis"
    RISK = "risk"
    REPORT = "report"
    COMPARE = "compare"
    TREND = "trend"
    CHART = "chart"


CN_NUM = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


SYSTEM_PROMPT = """你是一个金融数据分析助手。

你拥有以下工具：

1. sql_query
   查询结构化财务数据

2. rag_search
   查询研报、公告和行业分析

3. data_process
   计算增长率、毛利率、同比、排名等

4. chart
   生成折线图、柱状图等可视化图表

工具选择规则：

A. 查询财务指标
→ sql_query

B. 分析原因、业绩解读、风险分析
→ sql_query
→ rag_search

C. 计算类问题
→ sql_query
→ data_process

D. 图表类问题
→ sql_query
→ chart

E. 信息不足
→ 向用户澄清

执行规则：

- 每轮只能调用一个工具
- 工具返回结果后继续推理
- 可以连续调用多个工具完成任务
- 不要重复调用相同工具
- 获得足够信息后立即结束

结束格式：

{
  "action":"FINISH",
  "answer":"..."
}

工具格式：

{
  "tool":"工具名",
  "inputs":{...}
}
"""


class FinanceAgent:
    def __init__(self):
        self.llm = LLMClient()
        self.session_mgr = get_session_manager()
        self.tools = {
            "sql_query": SQLTool(),
            "rag_search": RAGTool(),
            "data_process": DataTool(),
            "chart": ChartTool(),
            "risk_analysis": RiskTool(),
            "report": ReportTool(),
        }

    def _normalize_time(self, question):
        now = datetime.now()
        m = re.search(r"(?:近|最近|过去)([一二三四五六七八九十\d]+)年", question)
        if m:
            token = m.group(1)
            n = int(token) if token.isdigit() else CN_NUM.get(token, 3)
            end_year = now.year - 1  # 不包含当前年（数据可能不完整）
            start_year = end_year - n + 1
            return question.replace(m.group(0), f"{start_year}年到{end_year}年")
        return question

    def _classify(self, question):
        if any(kw in question for kw in ["风险", "预警", "问题", "异常", "隐患"]):
            return TaskType.RISK
        if any(kw in question for kw in ["报告", "报告书", "财务分析", "生成报告"]):
            return TaskType.REPORT
        if any(kw in question for kw in ["为什么", "原因", "为何", "分析", "解释", "说明", "看法", "归因"]):
            return TaskType.ANALYSIS
        if any(kw in question for kw in ["趋势", "走势", "变化", "逐年", "近年", "历年"]):
            return TaskType.TREND
        if any(kw in question for kw in ["对比", "比较", "vs", "与", "和", "跟"]):
            return TaskType.COMPARE
        if any(kw in question for kw in ["图", "图表", "折线", "柱状", "饼图", "可视化", "画"]):
            return TaskType.CHART
        return TaskType.QUERY

    def _check_slots(self, question, task_type):
        """轻量级槽位检查：只在明确缺信息时才提示，不拦截排名/统计类查询"""
        has_indicator = any(kw in question for kw in [
            "净利润", "营收", "收入", "利润", "资产", "负债",
            "现金流", "每股", "ROE", "毛利", "净利", "同比", "环比",
            "研发", "销售费用", "毛利率", "净利率", "资产负债率", "总资产",
            "排名", "前五", "前十", "前三", "最高", "最低",
        ])

        # 只有QUERY类型且完全没有指标关键词时才提示
        if task_type == TaskType.QUERY and not has_indicator:
            has_company = bool(re.search(
                r"白云山|云南白药|华润三九|同仁堂|片仔癀|金花|999|白药|三金|同仁",
                question
            ))
            if has_company:
                return "请提供要查询的指标，例如'净利润'、'营收'"

        return None

    def _handle_followup(self, question, session, session_id):
        """追问检测：在分类和槽位检查之前执行，判断是否是上一轮的追问"""
        last = session.history[-1]
        last_data = last.get("data")
        last_slots = last.get("slots", {})
        last_question = last.get("question", "")

        # 追问关键词检测
        chart_kws = ["图片", "画图", "图表", "柱状图", "折线图", "饼图", "可视化", "画一下", "画个图"]
        report_kws = ["报告", "报告书", "生成报告", "导出报告"]
        rank_kws = ["第二名", "第三名", "第四名", "第五名", "前三", "前五", "前十",
                    "第一", "第二", "第三", "第四", "第五", "最后一名", "排名"]

        # 1. 图表追问：用上一轮数据直接生成图表
        if any(kw in question for kw in chart_kws):
            if last_data and isinstance(last_data, list) and len(last_data) >= 2:
                indicator = last_slots.get("indicator", "")
                # 检测用户是否指定了图表类型
                chart_type = self._detect_chart_type_from_text(question)
                chart_html = self._generate_chart(last_data, indicator, chart_type=chart_type)
                return {
                    "answer": "根据上一轮查询结果生成图表：",
                    "task_type": "chart",
                    "plan": ["chart"],
                    "steps": 0,
                    "history": [],
                    "session_id": session_id,
                    "confidence": 0.90,
                    "chart": chart_html,
                }

        # 2. 报告追问：用上一轮的公司信息生成报告
        if any(kw in question for kw in report_kws):
            company = last_slots.get("company", "")
            if company:
                year = last_slots.get("year", self._extract_year(last_question))
                plan = ["report"]
                history, _ = self._execute_plan(plan, question, session, TaskType.REPORT,
                                               override_inputs={"company": company, "year": year})
                final_answer = self._summarize(question, history, TaskType.REPORT)
                session.add_turn(question, final_answer, session.slots)
                return {
                    "answer": final_answer,
                    "task_type": "report",
                    "plan": plan,
                    "steps": len(history),
                    "history": history,
                    "session_id": session_id,
                    "confidence": 0.85,
                }

        # 3. 排名追问：从上一轮数据中提取第N名
        if any(kw in question for kw in rank_kws) and last_data:
            rank_num = self._extract_rank_number(question)
            if rank_num and rank_num <= len(last_data):
                target = last_data[rank_num - 1]
                name = target.get("stock_abbr", "")
                val_keys = [k for k in target.keys()
                           if k not in ('stock_abbr', 'report_year', 'report_period', 'stock_code', 'rank', 'note')]
                detail = "、".join("%s=%s" % (k, target.get(k, "N/A")) for k in val_keys[:3])
                return {
                    "answer": "第%d名是 %s，%s" % (rank_num, name, detail),
                    "task_type": "query",
                    "plan": [],
                    "steps": 0,
                    "history": [],
                    "session_id": session_id,
                    "confidence": 0.95,
                }

        return None

    def _extract_rank_number(self, question):
        """从问题中提取排名数字"""
        import re
        CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                  "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        # "第二名" → 2
        m = re.search(r"第([一二三四五六七八九十\d]+)", question)
        if m:
            token = m.group(1)
            return int(token) if token.isdigit() else CN_NUM.get(token)
        # "前三" → 3, "前五" → 5
        m = re.search(r"前([一二三四五六七八九十\d]+)", question)
        if m:
            token = m.group(1)
            return int(token) if token.isdigit() else CN_NUM.get(token)
        return None

    def _detect_chart_type_from_text(self, question):
        """从用户文本中检测指定的图表类型"""
        # 优先匹配更具体的关键词（横向柱状图 > 柱状图）
        if any(kw in question for kw in ["横向柱状图", "横向柱状", "横向柱"]):
            return "hbar"
        if any(kw in question for kw in ["折线图", "折线", "趋势图"]):
            return "line"
        if any(kw in question for kw in ["柱状图", "柱状", "条形图"]):
            return "bar"
        if any(kw in question for kw in ["饼图", "饼状图", "环形图"]):
            return "pie"
        return None  # 未指定，使用智能选图

    def _plan(self, task_type):
        plans = {
            TaskType.QUERY:    ["sql_query"],
            TaskType.ANALYSIS: ["sql_query", "rag_search"],
            TaskType.RISK:     ["risk_analysis"],
            TaskType.REPORT:   ["report"],
            TaskType.TREND:    ["sql_query", "chart"],
            TaskType.CHART:    ["sql_query", "chart"],
            TaskType.COMPARE:  ["sql_query", "data_process"],
        }
        return plans.get(task_type, ["sql_query"])

    def _execute_plan(self, plan, question, session, task_type, override_inputs=None):
        history = []
        sql_data = None

        for step_idx, tool_name in enumerate(plan):
            print(f"\n[Agent] Plan Step {step_idx + 1}/{len(plan)}: {tool_name}")
            tool = self.tools.get(tool_name)
            if not tool:
                continue

            if tool_name == "sql_query":
                inputs = self._build_sql_inputs(question, task_type)
            elif tool_name == "rag_search":
                inputs = {"query": question, "company": ""}
                if sql_data:
                    slots = sql_data.get("slots", {})
                    if slots.get("company"):
                        inputs["company"] = slots["company"]
            elif tool_name == "data_process":
                data = sql_data.get("data", []) if sql_data else []
                metric = self._extract_metric(question)
                params = {"mode": "rank", "field": metric, "top_k": 10}
                inputs = {"data": data, "params": params, "action": "compare"}
            elif tool_name == "chart":
                data = sql_data.get("data", []) if sql_data else []
                # 智能选图：优先用用户指定的类型，否则自动检测
                chart_type = self._detect_chart_type_from_text(question)
                if not chart_type:
                    chart_type = "line" if task_type == TaskType.TREND else "bar"
                inputs = {"data": data, "chart_type": chart_type, "title": question}
            elif tool_name == "risk_analysis":
                data = sql_data.get("data", []) if sql_data else []
                company = self._extract_company(question)
                year = self._extract_year(question)
                inputs = {"company": company, "year": year, "data": data}
                if override_inputs:
                    inputs.update(override_inputs)
            elif tool_name == "report":
                company = self._extract_company(question)
                year = self._extract_year(question)
                inputs = {"company": company, "year": year}
                if override_inputs:
                    inputs.update(override_inputs)
            else:
                inputs = {}

            result = tool.run(inputs)
            success = result.get("success", False)
            print(f"  Result: success={success}")

            if tool_name == "sql_query":
                sql_data = result

            history.append({
                "step": step_idx + 1,
                "tool": tool_name,
                "success": success,
                "result": result,
            })

            if not success and tool_name == "sql_query":
                break

        return history, sql_data

    def _build_sql_inputs(self, question, task_type):
        """根据任务类型构造sql_query的输入"""
        if task_type == TaskType.TREND:
            company = self._extract_company(question)
            metric = self._extract_metric(question)
            year_start, year_end = self._extract_year_range(question)
            return {
                "question": question,
                "query_type": "trend",
                "company": company,
                "metric": metric,
                "year_start": year_start,
                "year_end": year_end,
            }
        elif task_type == TaskType.COMPARE:
            companies = self._extract_companies(question)
            metric = self._extract_metric(question)
            year = self._extract_year(question)
            return {
                "question": question,
                "query_type": "compare",
                "companies": companies,
                "metric": metric,
                "year": year,
            }
        else:
            return {"question": question}

    def _extract_company(self, question):
        """提取第一个公司名"""
        names = ["白云山", "云南白药", "华润三九", "同仁堂", "太极集团", "片仔癀",
                 "金花股份", "金花", "葵花药业", "达仁堂", "天士力", "康恩贝",
                 "东阿阿胶", "江中药业", "健民集团", "千金药业", "羚锐制药"]
        for n in names:
            if n in question:
                return n
        return ""

    def _extract_companies(self, question):
        """提取所有公司名"""
        names = ["白云山", "云南白药", "华润三九", "同仁堂", "太极集团", "片仔癀",
                 "金花股份", "金花", "葵花药业", "达仁堂", "天士力", "康恩贝",
                 "东阿阿胶", "江中药业", "健民集团", "千金药业", "羚锐制药"]
        return [n for n in names if n in question]

    def _extract_metric(self, question):
        """提取指标对应的数据库字段名"""
        mapping = {
            "净利润": "net_profit", "净利": "net_profit", "归母净利润": "net_profit",
            "营收": "total_operating_revenue", "营业收入": "total_operating_revenue",
            "收入": "total_operating_revenue", "营业总收入": "total_operating_revenue",
            "利润总额": "total_profit",
            "总资产": "asset_total_assets", "负债": "liability_total_liabilities",
            "净资产": "equity_total_equity",
            "经营现金流": "operating_cf_net_amount",
            "现金流": "operating_cf_net_amount",
            "毛利率": "gross_profit_margin", "净利率": "net_profit_margin",
            "资产负债率": "asset_liability_ratio",
            "ROE": "roe_weighted_excl_non_recurring",
        }
        # 先匹配长的关键词（"净利润"优先于"利润"）
        for kw in sorted(mapping.keys(), key=len, reverse=True):
            if kw in question:
                return mapping[kw]
        return "net_profit"

    def _extract_year(self, question):
        """提取年份"""
        import re
        m = re.search(r"(20\d{2})年?", question)
        return int(m.group(1)) if m else 2024

    def _extract_year_range(self, question):
        """提取年份范围"""
        import re
        now = datetime.now()
        m = re.search(r"(20\d{2})年到(20\d{2})年", question)
        if m:
            return int(m.group(1)), int(m.group(2))
        m = re.search(r"(20\d{2})年?", question)
        if m:
            y = int(m.group(1))
            return y, y
        return now.year - 2, now.year

    def query(self, question, session_id="default"):
        question = self._normalize_time(question)
        session = self.session_mgr.get_or_create(session_id)

        # ===== P0: 追问检测前置（在分类和槽位检查之前）=====
        if session.history:
            followup = self._handle_followup(question, session, session_id)
            if followup:
                return followup

        task_type = self._classify(question)
        clarify = self._check_slots(question, task_type)
        if clarify:
            return {"answer": clarify, "steps": 0, "history": [], "session_id": session_id}

        if session.is_followup(question) and session.slots.get("company"):
            merged = session.merge_question(question)
            question = question + "（上下文：公司=%s, 指标=%s）" % (
                merged.get("company", ""), merged.get("indicator", "")
            )

        plan = self._plan(task_type)
        print(f"\n[Agent] Task: {task_type.value}, Plan: {' → '.join(plan)}")
        history, sql_data = self._execute_plan(plan, question, session, task_type)

        # 所有任务都过LLM润色
        final_answer = self._summarize(question, history, task_type)

        # 自动生成图表（数据>=3行时）
        chart_html = None
        if sql_data and sql_data.get("data") and len(sql_data["data"]) >= 3:
            chart_html = self._generate_chart(sql_data["data"], self._extract_metric(question))

        if sql_data:
            session.update_slots(sql_data.get("slots", {}))
            # 保存本轮数据供追问使用
            session.add_turn(question, final_answer, session.slots,
                           data=sql_data.get("data"), task_type=task_type.value)
        else:
            session.add_turn(question, final_answer, session.slots,
                           task_type=task_type.value)

        # 按任务类型设置置信度
        confidence_map = {
            TaskType.QUERY: 0.95,
            TaskType.TREND: 0.90,
            TaskType.COMPARE: 0.90,
            TaskType.RISK: 0.90,
            TaskType.REPORT: 0.85,
            TaskType.ANALYSIS: 0.75,
            TaskType.CHART: 0.90,
        }
        confidence = confidence_map.get(task_type, 0.80)

        return {
            "answer": final_answer,
            "task_type": task_type.value,
            "plan": plan,
            "steps": len(history),
            "history": history,
            "session_id": session_id,
            "confidence": confidence,
            "chart": chart_html,
        }

    def _summarize(self, question, history, task_type=None):
        parts = [f"用户问题：{question}"]

        for h in history:
            r = h["result"]
            tool = h.get("tool", "")

            # 风险预警：结构化数据 + 润色指令
            if tool == "risk_analysis" and r.get("success"):
                alerts = r.get("alerts", [])
                parts.append("\n### 以下风险已由规则引擎确定（不可修改）：")
                if alerts:
                    for i, a in enumerate(alerts, 1):
                        parts.append("%d. [%s] %s: %s" % (i, a["severity"], a["rule"], a["detail"]))
                        parts.append("   建议：%s" % a["suggestion"])
                else:
                    parts.append("无风险")
                parts.append("\n要求：用专业财务分析语言改写上述结果。禁止新增/删除/修改风险项和等级。保留所有数字。")

            # 报告：结构化报告 + 润色指令
            elif tool == "report" and r.get("success"):
                report = r.get("report", "")
                parts.append("\n### 以下报告已由系统生成（数据不可修改）：")
                parts.append(report)
                parts.append("\n要求：用专业财务分析师的语言润色上述报告。保留所有数据和结论，让表达更自然专业。禁止修改任何数字或结论。")

            # 普通查询
            elif r.get("success"):
                data = r.get("data") or r.get("results") or []
                unit = r.get("unit", "")
                if unit:
                    parts.append(f"数据单位：{unit}")
                parts.append(json.dumps(data[:5], ensure_ascii=False, default=str)[:800])
            else:
                parts.append(f"无结果: {r.get('error', '')}")

        parts.append("\n请根据以上数据用自然语言回答。保留所有数字和结论，只改表达方式。直接输出答案，不要输出JSON。")
        context = "\n".join(parts)

        raw = self.llm.chat(SYSTEM_PROMPT, context, temperature=0.3)

        # 解析LLM输出：如果包含JSON，提取answer字段；否则用原始文本
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and "answer" in parsed:
                return parsed["answer"]
        except:
            pass

        # 尝试从文本中提取JSON块
        json_match = re.search(r'\{[^{}]*"answer"[^{}]*\}', raw, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
                if "answer" in parsed:
                    # 返回JSON之前的文本 + answer内容
                    prefix = raw[:json_match.start()].strip()
                    answer = parsed["answer"]
                    return f"{prefix}\n\n{answer}" if prefix else answer
            except:
                pass

        return raw

    def _extract_risk_answer(self, history):
        """从risk_analysis结果中提取结构化风险报告"""
        for h in history:
            if h.get("tool") == "risk_analysis" and h.get("result", {}).get("success"):
                r = h["result"]
                alerts = r.get("alerts", [])
                summary = r.get("summary", "")
                company = r.get("company", "")

                if not alerts:
                    return "%s财务状况良好，6项风险规则均未触发。" % company

                parts = ["## %s 风险预警报告\n" % company]
                high = [a for a in alerts if a["severity"] == "高"]
                mid = [a for a in alerts if a["severity"] == "中"]
                low = [a for a in alerts if a["severity"] == "低"]

                parts.append("发现 %d 项风险：\n" % len(alerts))
                if high:
                    parts.append("**【高风险】**")
                    for a in high:
                        parts.append("- %s：%s" % (a["rule"], a["detail"]))
                        parts.append("  建议：%s" % a["suggestion"])
                if mid:
                    parts.append("\n**【中风险】**")
                    for a in mid:
                        parts.append("- %s：%s" % (a["rule"], a["detail"]))
                        parts.append("  建议：%s" % a["suggestion"])
                if low:
                    parts.append("\n**【低风险】**")
                    for a in low:
                        parts.append("- %s：%s" % (a["rule"], a["detail"]))
                        parts.append("  建议：%s" % a["suggestion"])

                parts.append("\n---\n**综合评价：%s**" % summary)
                return "\n".join(parts)

        return "风险分析未返回结果。"

    def _extract_report_answer(self, history):
        """从report结果中提取报告"""
        for h in history:
            if h.get("tool") == "report" and h.get("result", {}).get("success"):
                return h["result"].get("report", "报告生成失败。")
        return "报告生成未返回结果。"

    def _generate_chart(self, data, indicator="", chart_type=None):
        """根据数据自动生成图表，支持智能选图和手动指定"""
        if not data or len(data) < 2:
            return None
        try:
            from chart_module import bar_chart, line_chart, hbar_chart, pie_chart

            val_keys = [k for k in data[0].keys()
                       if k not in ('stock_abbr', 'report_year', 'report_period', 'stock_code', 'rank', 'note')]
            if not val_keys:
                return None
            vk = val_keys[0]

            # 手动指定图表类型
            if chart_type:
                return self._render_chart(data, vk, chart_type, indicator)

            # 智能选图
            chart_type = self._detect_chart_type(data, vk)
            return self._render_chart(data, vk, chart_type, indicator)

        except Exception as e:
            print("[Chart] Error: %s" % e)
            return None

    def _detect_chart_type(self, data, value_key):
        """根据数据特征智能选择图表类型"""
        # 1. 检测是否是时间序列数据（同一公司多年数据）
        years = set(r.get("report_year") for r in data if r.get("report_year"))
        companies = set(r.get("stock_abbr") for r in data if r.get("stock_abbr"))

        if len(years) >= 3 and len(companies) <= 2:
            return "line"

        # 2. 检测是否是占比数据（百分比总和≈100%）
        values = [float(r.get(value_key, 0) or 0) for r in data]
        total = sum(abs(v) for v in values)
        if total > 0:
            # 检查是否所有值都是0-1之间的小数（百分比格式）
            all_pct = all(0 <= abs(v) <= 1 for v in values if v != 0)
            if all_pct and abs(sum(values) - 1.0) < 0.1:
                return "pie"
            # 或者值总和接近100
            if 95 <= total <= 105 and all(0 <= v <= 100 for v in values):
                return "pie"

        # 3. 检测标签长度（长标签用横向柱状图）
        labels = [str(r.get("stock_abbr", r.get("report_year", "?"))) for r in data]
        max_label_len = max(len(l) for l in labels) if labels else 0
        if max_label_len > 8:
            return "hbar"

        # 4. 默认：柱状图
        return "bar"

    def _render_chart(self, data, value_key, chart_type, indicator=""):
        """渲染指定类型的图表"""
        from chart_module import bar_chart, line_chart, hbar_chart, pie_chart

        labels = [str(r.get("stock_abbr", r.get("report_year", "?"))) for r in data[:10]]
        values = [float(r.get(value_key, 0) or 0) for r in data[:10]]

        if chart_type == "line":
            # 折线图：x轴用年份
            x_labels = [str(r.get("report_year", "?")) for r in data[:10]]
            return line_chart(x_labels, values, title=indicator or "趋势图", ylabel="万元")
        elif chart_type == "pie":
            return pie_chart(labels, values, title=indicator or "占比分析")
        elif chart_type == "hbar":
            return hbar_chart(labels, values, title=indicator or "数据对比", xlabel="万元")
        else:  # bar
            return bar_chart(labels, values, title=indicator or "数据对比", ylabel="万元")


if __name__ == "__main__":
    agent = FinanceAgent()
    print("金融问答Agent已启动")
    while True:
        try:
            q = input("\n>>> ").strip()
            if q.lower() in ("exit", "quit", "退出"):
                break
            if not q:
                continue
            result = agent.query(q)
            print(f"\n[{result.get('task_type','?')}] {'→'.join(result.get('plan',[]))}")
            print(f"{result['answer']}")
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"出错: {e}")