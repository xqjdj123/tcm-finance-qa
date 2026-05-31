# -*- coding: utf-8 -*-
"""Flask Web应用：调用Agent处理查询"""
import os, sys, json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "models", "model2_question_understanding"))
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# ===== Agent =====
_agent = None

def get_agent():
    global _agent
    if _agent is None:
        print("[Agent] Loading...")
        from agent.agent import FinanceAgent
        _agent = FinanceAgent()
        print("[Agent] Ready!")
    return _agent


# ===== 数字格式化 =====
def format_smart_number(num, field_name=""):
    if num is None: return "N/A"
    try:
        n = float(num)
        RATIO_FIELDS = {'asset_liability_ratio', 'gross_profit_margin', 'net_profit_margin',
                        'roe', 'roe_weighted_excl_non_recurring'}
        if field_name in RATIO_FIELDS:
            if n == 0: return "0.00%"
            if abs(n) < 1: return "{:.2f}%".format(n * 100)
            if abs(n) < 100: return "{:.2f}%".format(n)
        if abs(n) >= 10000: return "{:.2f}".format(n / 10000) + " 亿元"
        elif abs(n) >= 1: return "{:,.2f}".format(n) + " 万元"
        else: return "{:.4f}".format(n)
    except:
        return str(num)


# ===== 字段中文名 =====
FIELD_CN = {
    "total_operating_revenue": "营业总收入", "net_profit": "净利润",
    "total_profit": "利润总额", "asset_total_assets": "总资产",
    "liability_total_liabilities": "总负债", "equity_total_equity": "净资产",
    "operating_cf_net_amount": "经营现金流", "roe_weighted_excl_non_recurring": "ROE",
    "gross_profit_margin": "毛利率", "net_profit_margin": "净利率",
    "eps": "每股收益", "asset_inventory": "存货",
    "asset_accounts_receivable": "应收账款", "liability_short_term_loans": "短期借款",
    "asset_cash_and_cash_equivalents": "货币资金",
}


def build_html_table(data, title=""):
    """从agent返回的data构建HTML表格"""
    if not data or not isinstance(data, list):
        return ""

    val_keys = [k for k in data[0].keys() if k not in ('stock_abbr', 'report_year', 'report_period', 'stock_code', 'rank', 'note')]

    parts = ['<div class="result-table-wrap"><table class="result-table">']
    parts.append('<caption>%s</caption>' % title)
    parts.append('<thead><tr><th>公司</th><th>年份</th>')
    for k in val_keys:
        parts.append('<th>%s</th>' % FIELD_CN.get(k, k))
    parts.append('</tr></thead><tbody>')

    for row in data[:10]:
        name = row.get("stock_abbr", "")
        yr = row.get("report_year", "")
        rp = row.get("report_period", "")
        note = row.get("note", "")
        yl = str(yr) + "年" + (" " + rp if rp else "")
        parts.append('<tr><td>%s</td><td>%s</td>' % (name, yl))
        for k in val_keys:
            parts.append('<td>%s</td>' % format_smart_number(row.get(k), k))
        parts.append('</tr>')
        if note:
            parts.append('<tr><td colspan="%d" class="note">%s</td></tr>' % (len(val_keys) + 2, note))

    parts.append('</tbody></table></div>')
    return "".join(parts)


def build_report_html(report_md):
    """把Markdown报告转成简单HTML"""
    if not report_md:
        return ""
    html = report_md
    # 标题
    html = html.replace("# ", "<h2>").replace("\n", "</h2>\n", 1) if html.startswith("#") else html
    html = html.replace("## ", "<h3>").replace("\n", "</h3>\n")
    # 加粗
    import re
    html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
    # 表格
    lines = html.split("\n")
    result = []
    in_table = False
    for line in lines:
        if "|" in line and "---" not in line:
            if not in_table:
                result.append('<table class="report-table">')
                in_table = True
            cells = [c.strip() for c in line.split("|") if c.strip()]
            tag = "th" if not any(c.replace(".", "").replace(",", "").replace("-", "").isdigit() for c in cells) else "td"
            result.append("<tr>" + "".join("<%s>%s</%s>" % (tag, c, tag) for c in cells) + "</tr>")
        else:
            if in_table:
                result.append("</table>")
                in_table = False
            result.append(line)
    if in_table:
        result.append("</table>")
    return "<br>".join(result)


# ===== 核心处理 =====
def process_question(question, session_id="default"):
    agent = get_agent()
    result = agent.query(question, session_id=session_id)

    answer = result.get("answer", "")
    task_type = result.get("task_type", "")
    plan = result.get("plan", [])
    history = result.get("history", [])
    chart_html = result.get("chart")  # agent返回的图表

    # 从history中提取数据
    sql_data = None
    report_md = None
    risk_alerts = None

    for h in history:
        r = h.get("result", {})
        if h.get("tool") == "sql_query" and r.get("data"):
            sql_data = r["data"]
        if h.get("tool") == "report" and r.get("report"):
            report_md = r["report"]
        if h.get("tool") == "risk_analysis" and r.get("alerts"):
            risk_alerts = r["alerts"]

    # 构建展示内容
    display_html = ""

    if report_md:
        # 报告模式：直接展示报告
        display_html = build_report_html(report_md)
    elif sql_data:
        # 数据模式：展示表格
        display_html = build_html_table(sql_data, title=question)

    # 风险预警
    rag_html = None
    if risk_alerts:
        parts = ['<div class="risk-section"><b>风险预警</b>']
        for a in risk_alerts:
            sev_class = {"高": "high", "中": "mid", "低": "low"}.get(a["severity"], "low")
            parts.append('<div class="risk-item %s"><span class="risk-sev">[%s]</span> %s：%s</div>' % (
                sev_class, a["severity"], a["rule"], a["detail"]))
        parts.append('</div>')
        rag_html = "".join(parts)

    return {
        "question": question,
        "task_type": task_type,
        "intent": task_type,  # 前端兼容
        "plan": " → ".join(plan),
        "confidence": result.get("confidence", 0.5),
        "answer": answer,
        "display_html": display_html,
        "rag_html_raw": rag_html,
        "chart": chart_html,
        "result_count": len(sql_data) if sql_data else 0,
    }


# ===== Flask路由 =====
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/query", methods=["POST"])
def query():
    d = request.get_json()
    q = d.get("question", "").strip()
    if not q:
        return jsonify({"error": "empty"}), 400
    # 用客户端IP作为session_id，保持同一用户的对话上下文
    session_id = request.remote_addr or "default"
    return jsonify(process_question(q, session_id=session_id))

@app.route("/api/reset", methods=["POST"])
def reset():
    global _agent
    if _agent:
        _agent.session_mgr.reset("default")
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    print("=" * 50)
    print("  Flask app http://127.0.0.1:7860")
    print("=" * 50)
    app.run(host="127.0.0.1", port=7860, debug=False)
