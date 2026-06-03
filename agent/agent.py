# -*- coding: utf-8 -*-
"""Finance Agent: 依赖 pipeline.understand() 做统一理解，不再自行分类/解析"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.config import MAX_STEPS
from agent.llm import LLMClient
from agent.session import get_session_manager
from agent.followup_resolver import FollowupResolver, FollowupType
from agent.tools.sql_tool import SQLTool
from agent.tools.rag_tool import RAGTool
from agent.tools.data_tool import DataTool
from agent.tools.chart_tool import ChartTool
from agent.tools.risk_tool import RiskTool
from agent.tools.report_tool import ReportTool
from agent.kg_resolver import get_kg_resolver
from agent.intent_splitter import IntentSplitter
from agent.result_merger import ResultMerger


class FinanceAgent:
    def __init__(self):
        self.llm = LLMClient()
        self.session_mgr = get_session_manager()
        self.followup_resolver = FollowupResolver()
        self._pipeline = None  # shared pipeline instance
        self.tools = {
            "sql_query": SQLTool(),
            "rag_search": RAGTool(),
            "data_process": DataTool(),
            "chart": ChartTool(),
            "risk_analysis": RiskTool(),
            "report": ReportTool(),
        }

    def _get_pipeline(self):
        if self._pipeline is None:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "models", "model2_question_understanding"))
            from pipeline import FinancialQAPipeline
            self._pipeline = FinancialQAPipeline()
        return self._pipeline

    # 英文字段名 → 中文标签（用于图表标题和展示）
    METRIC_EN_TO_CN = {
        "total_operating_revenue": "营业总收入",
        "operating_expense_cost_of_sales": "营业成本",
        "operating_expense_selling_expenses": "销售费用",
        "operating_expense_administrative_expenses": "管理费用",
        "operating_expense_financial_expenses": "财务费用",
        "operating_expense_rnd_expenses": "研发费用",
        "operating_expense_taxes_and_surcharges": "税金及附加",
        "total_operating_expenses": "营业总成本",
        "operating_profit": "营业利润",
        "total_profit": "利润总额",
        "net_profit": "净利润",
        "asset_impairment_loss": "资产减值损失",
        "credit_impairment_loss": "信用减值损失",
        "other_income": "其他收益",
        "asset_cash_and_cash_equivalents": "货币资金",
        "asset_accounts_receivable": "应收账款",
        "asset_inventory": "存货",
        "asset_trading_financial_assets": "交易性金融资产",
        "asset_construction_in_progress": "在建工程",
        "asset_total_assets": "资产总计",
        "asset_total_assets_yoy_growth": "总资产同比增长率",
        "liability_accounts_payable": "应付账款",
        "liability_advance_from_customers": "预收账款",
        "liability_contract_liabilities": "合同负债",
        "liability_short_term_loans": "短期借款",
        "liability_total_liabilities": "负债合计",
        "liability_total_liabilities_yoy_growth": "负债合计同比增长率",
        "asset_liability_ratio": "资产负债率",
        "equity_unappropriated_profit": "未分配利润",
        "equity_total_equity": "所有者权益合计",
        "net_cash_flow": "净现金流",
        "net_cash_flow_yoy_growth": "净现金流同比增长率",
        "operating_cf_net_amount": "经营性现金流净额",
        "operating_cf_ratio_of_net_cf": "经营性现金流占比",
        "operating_cf_cash_from_sales": "经营性现金流入",
        "investing_cf_net_amount": "投资性现金流净额",
        "investing_cf_ratio_of_net_cf": "投资性现金流占比",
        "investing_cf_cash_for_investments": "投资性现金流出",
        "investing_cf_cash_from_investment_recovery": "投资性现金流入",
        "financing_cf_cash_from_borrowing": "融资性现金流入",
        "financing_cf_cash_for_debt_repayment": "融资性现金流出",
        "financing_cf_net_amount": "融资性现金流净额",
        "financing_cf_ratio_of_net_cf": "融资性现金流占比",
        "total_share_capital": "总股本",
        "eps": "每股收益",
        "operating_revenue_yoy_growth": "营收同比增长率",
        "operating_revenue_qoq_growth": "营收环比增长率",
        "net_profit_yoy_growth": "净利润同比增长率",
        "net_profit_qoq_growth": "净利润环比增长率",
        "net_asset_per_share": "每股净资产",
        "roe": "净资产收益率",
        "operating_cf_per_share": "每股经营现金流",
        "net_profit_excl_non_recurring": "扣非净利润",
        "net_profit_excl_non_recurring_yoy": "扣非净利润同比增长率",
        "gross_profit_margin": "销售毛利率",
        "net_profit_margin": "销售净利率",
        "roe_weighted_excl_non_recurring": "加权平均净资产收益率",
    }

    INTENT_TO_TASKTYPE = {
        "basic_query": "query",
        "time_trend": "trend",
        "comparison": "compare",
        "stat_query": "query",
        "analysis_query": "analysis",
        "fuzzy_intent": "query",
    }

    # Step config: required=True means plan stops on failure, False means continue
    STEP_CONFIG = {
        "sql_query": {"required": False},       # SQL failure -> continue (RAG may still help)
        "rag_search": {"required": False},       # RAG failure -> continue (SQL result may be enough)
        "chart": {"required": True},             # Chart failure with no data -> stop
        "data_process": {"required": False},     # Data calc failure -> continue
        "risk_analysis": {"required": True},     # Risk failure -> stop (no useful output)
        "report": {"required": True},            # Report failure -> stop
    }

    # Plan by intent (from pipeline.model2, not Agent._classify)
    INTENT_PLANS = {
        "basic_query":     ["sql_query"],
        "time_trend":      ["sql_query", "chart"],
        "comparison":      ["sql_query", "data_process"],
        "stat_query":      ["sql_query"],
        "analysis_query":  ["sql_query", "rag_search"],
        "fuzzy_intent":    ["sql_query"],
    }

    def _plan_for_intent(self, intent, understanding, question=None):
        """plan by intent, fallback to keyword match for uncertain intents"""
        if intent == "open_question":
            return ["rag_search"]
        if intent == "unknown" or intent not in self.INTENT_PLANS:
            fb = self._fallback_intent(question) if question else None
            if fb == "open_question":
                return ["rag_search"]
            if fb:
                return self.INTENT_PLANS.get(fb, ["sql_query"])
        # basic_query: if question has analysis/RAG keywords and no company, append rag_search
        if intent == "basic_query" and question:
            has_company = bool(understanding.get("companies"))
            fb = self._fallback_intent(question)
            if not has_company:
                if fb == "open_question":
                    return ["rag_search"]
                if fb == "analysis_query":
                    return ["sql_query", "rag_search"]
        plans = self.INTENT_PLANS.get(intent, ["sql_query"])
        """根据 pipeline 解析的 intent 返回工具计划"""
        plans = self.INTENT_PLANS.get(intent, ["sql_query"])
        # 如果 pipeline 标记了 needs_chart，追加 chart 步骤
        if understanding.get("needs_chart") and "chart" not in plans:
            plans = plans + ["chart"]
        return plans

    ANALYSIS_KEYWORDS = ["分析", "原因", "为什么", "趋势", "现状", "影响", "格局"]
    RAG_KEYWORDS = ["行业", "竞争", "规模", "发展", "政策", "研发投入"]

    def _fallback_intent(self, question):
        """lightweight keyword fallback when Model2 returns generic intent"""
        if any(kw in question for kw in ["风险", "预警"]):
            return "risk_analysis"
        if any(kw in question for kw in ["报告", "生成报告"]):
            return "report"
        # Analysis keywords -> analysis_query (may include rag)
        if any(kw in question for kw in self.ANALYSIS_KEYWORDS):
            has_company = any(name in question for name in [
                "白云山", "云南白药", "华润三九", "同仁堂", "太极集团", "片仔癀", "东阿阿胶"])
            if not has_company:
                return "open_question"
            return "analysis_query"
        # Industry-level keywords without company -> RAG
        if any(kw in question for kw in self.RAG_KEYWORDS):
            has_company = any(name in question for name in [
                "白云山", "云南白药", "华润三九", "同仁堂", "太极集团", "片仔癀", "东阿阿胶"])
            if not has_company:
                return "open_question"
        return None

    def _intent_to_tasktype(self, intent):
        """pipeline intent -> Agent task_type string"""
        return self.INTENT_TO_TASKTYPE.get(intent, "query")

    def _handle_followup(self, question, session, session_id):
        """处理追问：基于 session.last_understanding 做继承"""
        if not session.history:
            return None

        last_und = session.get_last_understanding()
        if not last_und:
            return None

        resolved = self.followup_resolver.resolve(question, last_und)
        if not resolved["is_followup"]:
            return None

        # 用 resolve 结果重新理解
        merged_und = resolved["understanding"]
        session.update_understanding(merged_und)

        # 特殊追问类型
        ftype = resolved["type"]
        if ftype == FollowupType.CHART:
            last_data = session.get_last_data()
            if last_data:
                raw_ind = merged_und.get("indicators", [""])[0] if merged_und.get("indicators") else ""
                indicator_cn = self.METRIC_EN_TO_CN.get(raw_ind, raw_ind)
                chart_html = self._generate_chart(last_data, indicator_cn, chart_type=resolved.get("chart_type"))
                return {
                    "answer": f"已生成图表",
                    "task_type": "chart",
                    "plan": ["chart"],
                    "steps": 1, "history": [],
                    "session_id": session_id, "confidence": 0.85,
                    "chart": chart_html,
                }

        # 普通追问：走主流程重新 query
        return None

    def query(self, question, session_id="default"):
        session = self.session_mgr.get_or_create(session_id)

        # ===== P0: 追问检测 =====
        if session.history:
            followup = self._handle_followup(question, session, session_id)
            if followup:
                return followup

        # ===== P2: 多意图拆分 =====
        splitter = IntentSplitter()
        sub_questions = splitter.split(question)

        if len(sub_questions) > 1:
            # 多意图：循环执行每个子问题，然后合并结果
            results = []
            for sq in sub_questions:
                result = self._run_single(sq, session_id)
                if result:
                    results.append(result)

            # 合并结果
            merger = ResultMerger()
            merged_result = merger.merge(results, question)
            return merged_result

        # 单意图：走原有流程
        return self._run_single(question, session_id)

    def _run_single(self, question, session_id="default"):
        """处理单个子问题"""
        session = self.session_mgr.get_or_create(session_id)

        # ===== Step 1: 统一理解（pipeline 单次调用，包含 NER + Model2）=====
        pipeline = self._get_pipeline()
        understanding = pipeline.understand(question)

        # ===== Step 1.5: KG Resolver（同义词归一 + 层级展开）=====
        # 先将英文指标名转换为中文（因为 kg_resolver 期望中文输入）
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "models", "model2_question_understanding"))
        from field_dict import SCHEMA_DICT

        def _en_to_cn(en_name):
            """英文字段名 → 中文字段名"""
            if en_name in SCHEMA_DICT:
                return SCHEMA_DICT[en_name]["label"]
            return en_name

        def _cn_to_en(cn_name):
            """中文字段名 → 英文字段名"""
            for en, info in SCHEMA_DICT.items():
                if info["label"] == cn_name:
                    return en
            return cn_name

        # 转换为中文
        en_indicators = understanding.get("indicators", [])
        cn_indicators = [_en_to_cn(ind) for ind in en_indicators]

        # 调用 kg_resolver
        kg = get_kg_resolver()
        kg_result = kg.resolve(cn_indicators, question)

        # 将中文结果转换回英文
        resolved_en_indicators = [_cn_to_en(cn) for cn in kg_result["indicators"]]

        understanding["indicators"] = resolved_en_indicators
        understanding["indicators_cn"] = kg_result["indicators"]
        understanding["kg_expanded"] = kg_result["expanded"]
        understanding["kg_synonyms"] = kg_result["synonym_map"]
        # 多指标时标记
        if len(resolved_en_indicators) > 1:
            understanding["is_multi_indicator"] = True

        intent = understanding.get("intent", "basic_query")
        companies = understanding.get("companies", [])
        indicators = understanding.get("indicators", [])
        years = understanding.get("years", [])
        period = understanding.get("period")
        top_k = understanding.get("top_k")

        # 缓存理解结果到 session（供追问使用）
        session.update_understanding(understanding)

        task_type_str = self._intent_to_tasktype(intent)
        plan = self._plan_for_intent(intent, understanding, question)

        print(f"\n[Agent] Intent={intent}, Companies={companies}, Indicators={indicators}, Years={years}")
        print(f"[Agent] Plan: {' -> '.join(plan)}")

        history, sql_data = self._execute_plan(plan, question, understanding, session)

        # LLM 总结
        final_answer = self._summarize(question, history, intent)

        # 图表生成
        chart_html = None
        if "chart" in [h.get("tool") for h in history]:
            chart_in_history = [h for h in history if h.get("tool") == "chart"]
            if chart_in_history and chart_in_history[-1]["result"].get("success"):
                chart_html = chart_in_history[-1]["result"].get("chart_html")
        elif sql_data and sql_data.get("data") and len(sql_data["data"]) >= 2:
            raw_indicator = indicators[0] if indicators else ""
            indicator_cn = self.METRIC_EN_TO_CN.get(raw_indicator, raw_indicator)
            chart_html = self._generate_chart(sql_data["data"], indicator_cn)

        # 指标替换说明
        if sql_data and sql_data.get("substituted"):
            orig = sql_data.get("original_metric", "")
            display = sql_data.get("display_metric", "")
            if orig and display:
                note = f"注：数据库中无「{orig}」字段，已使用「{display}」代替，两者会计含义不同"
                final_answer += f"\n\n{note}"

        # 记录历史
        session.add_turn(question, final_answer, session.slots,
                       data=sql_data.get("data") if sql_data else None,
                       task_type=task_type_str)

        return {
            "answer": final_answer,
            "task_type": task_type_str,
            "plan": plan,
            "steps": len(history),
            "history": history,
            "session_id": session_id,
            "confidence": 0.85,
            "chart": chart_html,
        }

    def _execute_plan(self, plan, question, understanding, session):
        """执行计划步骤"""
        history = []
        sql_data = None

        for step_idx, tool_name in enumerate(plan):
            print(f"\n[Agent] Step {step_idx + 1}/{len(plan)}: {tool_name}")
            tool = self.tools.get(tool_name)
            if not tool:
                continue

            if tool_name == "sql_query":
                inputs = self._build_sql_inputs(question, understanding)
            elif tool_name == "rag_search":
                company = (understanding.get("companies") or [""])[0]
                inputs = {"query": question, "company": company}
            elif tool_name == "data_process":
                data = sql_data.get("data", []) if sql_data else []
                metric = (understanding.get("indicators") or ["net_profit"])[0]
                params = {"mode": "rank", "field": metric, "top_k": understanding.get("top_k", 10)}
                inputs = {"data": data, "params": params, "action": "compare"}
            elif tool_name == "chart":
                data = sql_data.get("data", []) if sql_data else []
                chart_type = self._detect_chart_type_from_text(question)
                if not chart_type:
                    chart_type = "line" if understanding.get("intent") == "time_trend" else "bar"
                inputs = {"data": data, "chart_type": chart_type, "title": question}
            elif tool_name == "risk_analysis":
                company = (understanding.get("companies") or [""])[0]
                inputs = {"company": company}
            elif tool_name == "report":
                company = (understanding.get("companies") or [""])[0]
                inputs = {"company": company}
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

            # Check if this step is required: required failure -> stop, optional failure -> continue
            if not success:
                step_required = self.STEP_CONFIG.get(tool_name, {}).get("required", True)
                if step_required:
                    break
                # optional step failed, continue to next

        return history, sql_data

    def _build_sql_inputs(self, question, understanding):
        """基于 understanding 构建 sql_query 输入，直接传递 understanding 避免重复解析"""
        return {
            "question": question,
            "query_type": "auto",
            "understanding": understanding,
        }

    def _summarize(self, question, history, intent):
        # 收集 pipeline 模板答案
        pipeline_answer = None
        for h in history:
            r = h.get("result", {})
            if h.get("tool") == "sql_query" and r.get("success"):
                pa = r.get("answer", "")
                if pa and "未查询到" not in pa:
                    pipeline_answer = pa

        # 检查是否有 RAG（研报）结果
        has_rag = any(
            h.get("tool") == "rag_search" and h.get("result", {}).get("success")
            and h["result"].get("results")
            for h in history
        )

        # 纯查数据：直接用 pipeline 模板答案，不经过 LLM
        if not has_rag and pipeline_answer:
            return pipeline_answer

        # 有研报：走 LLM 润色（整合数据 + 研报内容）
        return self._build_answer(question, history, fact_hint=pipeline_answer)

    def _format_data_for_llm(self, data):
        """将数据库原始数据转成中文字段名的可读文本，供LLM理解"""
        if not data:
            return "无数据"
        lines = []
        for row in data[:5]:
            parts = []
            name = row.get("stock_abbr", "")
            year = row.get("report_year", "")
            period = row.get("report_period", "")
            if name: parts.append(f"公司：{name}")
            if year: parts.append(f"年份：{year}")
            if period: parts.append(f"报告期：{period}")
            for k, v in row.items():
                if k in ('stock_abbr', 'report_year', 'report_period', 'stock_code', 'rank', 'note'):
                    continue
                cn = self.METRIC_EN_TO_CN.get(k, k)
                if v is not None and v != "":
                    parts.append(f"{cn}：{v}")
            note = row.get("note", "")
            if note: parts.append(f"备注：{note}")
            lines.append("，".join(parts))
        return "\n".join(lines)

    def _build_answer(self, question, history, fact_hint=None):
        parts = [f"用户问题：{question}"]
        if fact_hint:
            parts.append(f"已查到的数据事实：{fact_hint}")
        rag_texts = []
        for h in history:
            r = h["result"]
            tool = h.get("tool", "")
            if tool == "rag_search" and r.get("success"):
                results = r.get("results", [])
                if results:
                    parts.append("研报参考：")
                    for i, res in enumerate(results[:5], 1):
                        txt = res.get("text", "")[:500]
                        src = res.get("source", "")
                        parts.append(f"{i}. [{src}] {txt}")
                        rag_texts.append(txt)
            elif tool == "sql_query" and not r.get("success"):
                err = r.get("error", "")
                parts.append(f"查询失败：{err}")
            elif r.get("success"):
                data = r.get("data") or r.get("results") or []
                parts.append("查询结果：\n" + self._format_data_for_llm(data))
            else:
                parts.append(f"无结果 {r.get('error', '')}")
        # 检查是否有实质内容传给LLM
        has_data = any(h.get("result", {}).get("success") and (h["result"].get("data") or h["result"].get("results")) for h in history)
        if not has_data and not rag_texts and not fact_hint:
            return "未查询到相关数据，请尝试换个问法或指定具体公司和年份。"

        summary = "\n\n".join(parts)
        summary += "\n\n请根据以上数据用中文回答用户的问题，直接给出分析结论，不要复述原始数据。如果数据不足以回答，请如实说明。"
        try:
            ans = self.llm.chat("你是一个专业的财务分析助手。仅基于提供的数据回答，不要编造数据。", summary)
            if ans and ans.strip() and not ans.startswith("[LLM Error]"):
                return ans
            # LLM 失败，用 pipeline 答案或 RAG 兜底
            if fact_hint:
                return fact_hint
            if rag_texts:
                return "根据查询到的相关信息：\n" + "\n".join(rag_texts[:3])
            return "暂无相关数据，请尝试其他问法。"
        except Exception as e:
            if fact_hint:
                return fact_hint
            if rag_texts:
                return "根据查询到的相关信息：\n" + "\n".join(rag_texts[:3])
            return f"暂无相关数据，请尝试其他问法。({e})"
    def _detect_chart_type_from_text(self, question):
        if "折线" in question or "趋势" in question:
            return "line"
        if "柱状" in question:
            return "bar"
        if "饼" in question:
            return "pie"
        if "横向" in question:
            return "hbar"
        return None

    def _generate_chart(self, data, indicator="", chart_type=None):
        if not data or len(data) < 2:
            return None
        try:
            from chart_module import bar_chart, line_chart, hbar_chart, pie_chart
            val_keys = [k for k in data[0].keys()
                       if k not in ('stock_abbr', 'report_year', 'report_period', 'stock_code', 'rank', 'note')]
            if not val_keys:
                return None
            vk = val_keys[0]
            if chart_type:
                return self._render_chart(data, vk, chart_type, indicator)
            chart_type = self._detect_chart_type(data, vk)
            return self._render_chart(data, vk, chart_type, indicator)
        except Exception as e:
            print(f"[Chart] Error: {e}")
            return None

    def _detect_chart_type(self, data, value_key):
        years = set(r.get("report_year") for r in data if r.get("report_year"))
        companies = set(r.get("stock_abbr") for r in data if r.get("stock_abbr"))
        if len(years) >= 3 and len(companies) <= 2:
            return "line"
        values = [float(r.get(value_key, 0) or 0) for r in data]
        total = sum(abs(v) for v in values)
        if total > 0:
            all_pct = all(0 <= abs(v) <= 1 for v in values if v != 0)
            if all_pct and abs(sum(values) - 1.0) < 0.1:
                return "pie"
            if 95 <= total <= 105 and all(0 <= v <= 100 for v in values):
                return "pie"
        labels = [str(r.get("stock_abbr", r.get("report_year", "?"))) for r in data]
        max_label_len = max(len(l) for l in labels) if labels else 0
        if max_label_len > 8:
            return "hbar"
        return "bar"

    def _render_chart(self, data, value_key, chart_type, indicator=""):
        from chart_module import bar_chart, line_chart, hbar_chart, pie_chart
        labels = [str(r.get("stock_abbr", r.get("report_year", "?"))) for r in data[:10]]
        values = [float(r.get(value_key, 0) or 0) for r in data[:10]]
        if chart_type == "line":
            x_labels = [str(r.get("report_year", "?")) for r in data[:10]]
            return line_chart(x_labels, values, title=indicator or "趋势图", ylabel="万元")
        elif chart_type == "pie":
            return pie_chart(labels, values, title=indicator or "占比分析")
        elif chart_type == "hbar":
            return hbar_chart(labels, values, title=indicator or "数据对比", xlabel="万元")
        else:
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
            print(f"\n[{result.get('task_type','?')}] {' -> '.join(result.get('plan',[]))}")
            print(f"{result['answer']}")
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"出错: {e}")
            import traceback
            traceback.print_exc()
