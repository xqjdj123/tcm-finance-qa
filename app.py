# -*- coding: utf-8 -*-
"""Flask Web应用 — ReAct Agent 财务智能问答系统"""
import os
import sys
import json
import re
import time
import uuid
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "models", "model2_question_understanding"))

from flask import Flask, render_template, request, jsonify
app = Flask(__name__)


def _resolve_session_id(payload=None):
    """会话 ID 解析：请求体 session_id > cookie sid > 新生成 uuid。

    不再用 request.remote_addr：同一 NAT 出口下所有人共用一个会话，
    追问互相串台。前端可传 session_id，未传则用 cookie，仍无则新建。
    """
    payload = payload or {}
    sid = payload.get("session_id") or request.cookies.get("sid")
    return sid or uuid.uuid4().hex

# ===== Agent =====
_agent = None

def get_agent():
    global _agent
    if _agent is None:
        print("[Agent] Loading ReActAgent...")
        from agent.tools import register_all_tools
        register_all_tools()
        from agent.engine import ReActAgent
        _agent = ReActAgent()
        print(f"[Agent] ReActAgent Ready! (max {_agent.max_iterations} iterations)")
    return _agent


# ===== 数字格式化 (UnitConverter) =====
def format_smart_number(num, field_name=""):
    if num is None:
        return "N/A"
    try:
        from domain.units.converter import UnitConverter
        return UnitConverter.format_value(num, field_name)
    except Exception:
        try:
            n = float(num)
            if abs(n) >= 10000:
                return "{:.2f} 亿元".format(n / 10000)
            elif abs(n) >= 1:
                return "{:,.2f} 万元".format(n)
            else:
                return "{:.4f}".format(n)
        except Exception:
            return str(num)


# ===== 字段中文名 =====
def _load_field_cn():
    try:
        from agent.taxonomy import build_en_cn_map
        return build_en_cn_map()
    except Exception:
        pass
    return {
        "total_operating_revenue": "营业总收入", "net_profit": "净利润",
        "total_profit": "利润总额", "asset_total_assets": "总资产",
        "liability_total_liabilities": "总负债", "equity_total_equity": "净资产",
        "operating_cf_net_amount": "经营现金流",
    }

FIELD_CN = _load_field_cn()


# ===== 工具中文名（route / 轨迹展示）=====
# 键必须与 agent/tools/*.py 里的 `name = "..."` 逐字一致，
# 否则新工具只在轨迹里显示英文名，不报错但会悄悄降级。
TOOL_CN = {
    "sql_query": "SQL 查库",
    "rag_search": "研报检索",
    "chart": "图表生成",
    "data_process": "数据计算",
    "ner_extract": "实体识别",
    "model2_understand": "意图分类",
    "risk_analysis": "风险规则",
    "report": "报告生成",
}

# 前端「研报参考」页签最多渲染几条（检索本身 top_k 由 LLM 决定，可能更多）
_RAG_TOP_K_DISPLAY = 5


def _esc(value):
    """最小 HTML 转义。数据来自数据库与模型输出，含 & < > 会破坏表格结构。"""
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _inline(text):
    """行内标记渲染：**加粗** / `代码`。先转义再替换，避免注入。"""
    t = _esc(text)
    t = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)
    t = re.sub(r'`(.+?)`', r'<code>\1</code>', t)
    return t


# ===== HTML 模板工具 =====
_ROW_EXCLUDE = ('stock_abbr', 'report_year', 'report_period', 'stock_code',
                'rank', 'note', '_tbl_priority', '_rn', '_no_data')


def build_html_table(data, title=""):
    """SQL 结果 → HTML 明细表（列名走 taxonomy 中文映射，数值走 UnitConverter）。

    与 answer 的分工：answer 是模型的自然语言结论，本函数是「明细表」。
    两者此前在 process_question 里被写成 `if report_md / elif sql_data` 互斥，
    前端又用 `answer || display_html` 取值，而 answer 恒非空（兜底「暂无数据」）
    → 本函数与 report 渲染都是**永不执行的死代码**。现改为并列输出。
    """
    if not data or not isinstance(data, list):
        return ""
    val_keys = [k for k in data[0].keys() if k not in _ROW_EXCLUDE]
    if not val_keys:
        return ""
    rows = data[:10]
    parts = ['<div class="result-table-wrap"><table class="result-table">']
    if title:
        parts.append(f'<caption>{_esc(title)}</caption>')
    parts.append('<thead><tr><th>公司</th><th>年份</th>')
    for k in val_keys:
        parts.append(f'<th>{_esc(FIELD_CN.get(k, k))}</th>')
    parts.append('</tr></thead><tbody>')

    for row in rows:
        name = row.get("stock_abbr", "")
        yr = row.get("report_year", "")
        rp = row.get("report_period", "")
        note = row.get("note", "")
        yl = f"{yr}年 {rp}".strip() if yr else str(rp)
        parts.append(f'<tr><td>{_esc(name)}</td><td>{_esc(yl)}</td>')
        for k in val_keys:
            parts.append(f'<td>{_esc(format_smart_number(row.get(k), k))}</td>')
        parts.append('</tr>')
        if note:
            parts.append(
                f'<tr><td colspan="{len(val_keys) + 2}" class="note">'
                f'{_esc(note)}</td></tr>'
            )

    parts.append('</tbody></table>')
    # 截断必须说出来：此前静默 `data[:10]`，用户会以为库里只有 10 条
    if len(data) > len(rows):
        parts.append(
            f'<p class="table-footnote">共 {len(data)} 行，此处展示前 {len(rows)} 行；'
            f'完整结果见「SQL 溯源」页签</p>'
        )
    parts.append('</div>')
    return "".join(parts)


def build_report_html(report_md):
    """报告 Markdown → HTML。

    2026-09-17 重写。原实现两处硬伤（见 doc/修改总结/2026-09-17.md §1）：
      ① `html.replace("\\n", "</h3>\\n")` 没有 count 限制 → 正文每一行都被
         包成 `</h3>`，闭合标签还悬空，产出 HTML 结构完全错乱；
      ② 第 117、118 两行对同一字符串连续 replace，逻辑互相覆盖。
    现改为逐行状态机：标题 / 表格 / 列表 / 段落各归其位，无 `<br>` 拼接。
    """
    if not report_md:
        return ""
    out = []
    in_table = False

    def close_table():
        nonlocal in_table
        if in_table:
            out.append('</tbody></table>')
            in_table = False

    for raw in str(report_md).split("\n"):
        stripped = raw.strip()

        # Markdown 表格行：| a | b |
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            # 分隔行 |---|---| 丢弃
            if cells and all(set(c) <= set("-: ") for c in cells):
                continue
            if not in_table:
                out.append('<table class="report-table"><tbody>')
                in_table = True
            out.append("<tr>" + "".join(
                f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
            continue

        close_table()

        if not stripped:
            continue

        m = re.match(r'^(#{1,6})\s+(.*)$', stripped)
        if m:
            # `#` 映射到 h2（h1 被页面标题占用），更深的不超过 h6
            level = min(len(m.group(1)) + 1, 6)
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            continue

        if re.match(r'^[-*]\s+', stripped):
            out.append('<div class="md-li">'
                       + _inline(re.sub(r'^[-*]\s+', '', stripped))
                       + '</div>')
            continue

        out.append(f"<p>{_inline(stripped)}</p>")

    close_table()
    return "".join(out)


# ===== 运行状态 / 证据完备度（供前端状态与信任度展示）=====

def _period_label(row):
    """把 report_year + report_period 拼成 2025Q3 / 2024FY 这种可读口径。

    注意库里是**两列**（年份在 report_year，报告期在 report_period，
    取值为 FY|H1|Q1|Q3），前端此前要的 `result_period` 一直没人拼。
    """
    if not isinstance(row, dict):
        return ""
    yr, rp = row.get("report_year"), row.get("report_period")
    if not yr and not rp:
        return ""
    return f"{yr if yr is not None else ''}{rp or ''}"


_NUM_UNIT_RE = re.compile(r'^\s*(-?[\d,]+(?:\.\d+)?)\s*(.*)$')


def _period_note(period):
    """报告期口径提示 —— 界面必须写清，否则用户会误读。

    2026-09-17 实测确认（白云山 stock_abbr='白云山'）：
        2024 Q1 营收 229.5亿 < H1 410.4亿 < Q3 590.6亿 < FY 749.9亿
    单调递增 ⇒ 库里的 Q3 是**年初至三季度末的累计口径**（A 股披露惯例），
    不是「第三季度单季」。同理 H1 = 半年累计。

    这本身不是数据缺陷，但界面上如果只写「2025Q3」，
    用户会把前五名合计 1356 亿读成「一个季度做到的」。
    """
    p = str(period or "").upper()
    if p.endswith("Q3"):
        return "累计口径：年初至三季度末"
    if p.endswith("H1"):
        return "累计口径：年初至半年度末"
    if p.endswith("Q1"):
        return "单季口径：第一季度"
    if p.endswith("FY"):
        return "全年口径"
    return ""


def _split_num_unit(text):
    """拆开 format_smart_number 的输出：`"89.5010亿元"` → `("89.5010", "亿元")`。

    数值与单位分开，前端才能把数字放大字号、单位放小字号（设计稿 01 画板）。

    注意 `UnitConverter.format_value` 的输出**数值与单位之间没有空格**
    （见 domain/units/converter.py::format_value，且 |v|<100 时保留 4 位小数），
    所以这里必须用正则从右侧切分，不能按空格 split。
    小数位数与单位一律沿用该函数的输出 —— 它是项目里金额口径的唯一出口，
    前端不另做四舍五入，避免同一份数字出现两种口径。
    """
    m = _NUM_UNIT_RE.match(str(text))
    if not m:
        return str(text), ""
    return m.group(1), m.group(2).strip()


def _to_number(value):
    """把可能以字符串形式到达的数值转成 float，转不了返回 None。

    为什么需要这一步：`sql_tool` 返回的 JSON 里**数值是字符串**——
    MySQL DECIMAL → decimal.Decimal → 工具层 json 序列化时 default=str，
    到了 app.py 已经变成 `"6160599.38"`（2026-09-17 实测 resp1.json）。
    所以不能用 `isinstance(v, (int, float))` 判数值列，否则 KPI 会静默为空。

    这同时解释了图表工具为什么要「按值特征猜列」(_pick_value_key)：
    类型信息在工具层就丢了，下游只能靠值的形态反推。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


_RANKING_KPI_KEYWORDS = (
    "排名", "排行", "前十", "前五", "前三", "前3", "前5", "前10",
    "top", "占比", "集中度", "最高", "最低", "首位", "榜首",
)


def _is_ranking_like(question: str) -> bool:
    """是否排名/占比/集中度类问题（只有这类才适合出“首位/合计/集中度”卡）。"""
    q = str(question or "").lower()
    return any(k in q for k in _RANKING_KPI_KEYWORDS)


def _build_kpi(sql_data, tool_indicators=None, question=""):
    """从 SQL 结果派生 KPI 卡（设计稿 01 画板：结论先行的三张卡）。

    单位换算一律走 format_smart_number（= domain.units.converter.UnitConverter），
    **不在前端另写一套** —— 否则同一份数字会出现两种口径（万元 / 亿元各说各话）。

    规则：
      单行 → 每个数值列一张卡（最多 3 张）
      多行 → 仅排名/占比/集中度类问题出“首位/合计/首位集中度”卡，
             其余问题返回空 cards（避免问净利润却显示营业总收入）。
    """
    if not sql_data or not isinstance(sql_data, list):
        return {"cards": []}
    rows = sql_data[:50]
    if not isinstance(rows[0], dict):
        return {"cards": []}

    numeric = []
    for k in rows[0].keys():
        if k in _ROW_EXCLUDE:
            continue
        vals = [_to_number(r.get(k)) for r in rows]
        # 整列都能解析成数字，才算数值列（用 _to_number，不靠类型判断）
        if all(v is not None for v in vals):
            numeric.append(k)
    if not numeric:
        return {"cards": []}

    def fmt(v, key):
        return _split_num_unit(format_smart_number(v, key))

    if len(rows) == 1:
        cards = []
        for k in numeric[:3]:
            value, unit = fmt(rows[0].get(k), k)
            cards.append({
                "label": FIELD_CN.get(k, k), "value": value, "unit": unit,
                "note": _period_label(rows[0]) or "单期结果",
            })
        return {"cards": cards}

    # 多行：非排名/占比/集中度类问题直接不出三张卡。
    # 否则“统计净利润为负的公司数量”“满足三条件的名单”这类题也会
    # 强行显示“首位·营业总收入”，属于答非所问。
    if not _is_ranking_like(question):
        return {"cards": []}

    # 可比性守卫（2026-09-17 实测加）：只有在「同一报告期、一行一公司」时才做
    # 首位 / 合计 / 集中度。结果跨多个报告期时（如按公司逐年展开），合计会把
    # 同一公司不同期重复累加，得到一个没有业务含义的数 ——
    # 实测「中药行业研发投入现状分析」返回 670 行跨多年，若照算会显示
    # 「50 行合计 33.7 亿元」，而它是把同一批公司的不同年份加在一起。
    # 宁可不出卡，也不出一个错的数。
    periods = {(r.get("report_year"), r.get("report_period")) for r in rows}
    if len(periods) > 1:
        return {"cards": [],
                "skipped": f"结果跨 {len(periods)} 个报告期，未生成合计类指标"}

    def total(k):
        return sum(_to_number(r.get(k)) or 0.0 for r in rows)

    # 主指标优先跟用户问的指标走（tool_indicators 为中文标签），
    # 拿不到或该列非数值时才退回“合计最大的数值列”。
    cn_to_en = {cn: en for en, cn in FIELD_CN.items()}
    preferred = None
    for ind in (tool_indicators or []):
        en = cn_to_en.get(str(ind), str(ind))
        if en in numeric:
            preferred = en
            break
    primary = preferred or max(numeric, key=total)
    top = max(rows, key=lambda r: _to_number(r.get(primary)) or 0.0)
    s = total(primary)
    v_top, u_top = fmt(_to_number(top.get(primary)) or 0.0, primary)
    v_sum, u_sum = fmt(s, primary)
    pct = ((_to_number(top.get(primary)) or 0.0) / s * 100) if s else 0.0
    label = FIELD_CN.get(primary, primary)

    return {
        "cards": [
            {"label": f"首位 · {label}", "value": v_top, "unit": u_top,
             "note": f"{top.get('stock_abbr', '')} · {_period_label(top)}".strip(" ·")},
            {"label": f"{len(rows)} 行合计", "value": v_sum, "unit": u_sum,
             "note": f"字段：{label}"},
            {"label": "首位集中度", "value": f"{pct:.1f}", "unit": "%",
             "note": f"首位 ÷ 合计（{len(rows)} 行）"},
        ],
        "primary_field": primary,
        "count": len(rows),
    }


def _collect_rag_hits(tool_calls_log, limit=_RAG_TOP_K_DISPLAY):
    """摊平 rag_search 的结构化命中，供前端渲染「研报参考」页签。

    数据源：`domain/retriever/hybrid.py::search` 返回
    `{"results": [{source, company, year, section, text, score}],
      "count", "company_mode", "rerank_mode"}`。
    此前 app.py 只把 risk_analysis 的结果拼成字符串塞进 `rag_html_raw`，
    rag_search 的命中从未出过前端 —— 这正是「研报参考」页签永不出现的原因。

    返回 (命中列表, 检索模式 dict)。
    """
    hits, modes = [], None
    for tc in tool_calls_log:
        if tc.get("tool") != "rag_search" or tc.get("dup"):
            continue
        parsed = tc.get("result_parsed")
        if not isinstance(parsed, dict) or not parsed.get("results"):
            continue
        modes = {
            "company_mode": parsed.get("company_mode", ""),
            "rerank_mode": parsed.get("rerank_mode", ""),
        }
        for r in parsed["results"]:
            if not isinstance(r, dict):
                continue
            hits.append({
                "source": r.get("source", ""),
                "company": r.get("company", ""),
                "year": r.get("year", ""),
                "section": r.get("section", ""),
                "text": r.get("text", ""),
                "score": r.get("score", 0) or 0,
            })
    # 同一片段可能被多轮检索命中：按 (来源, 正文前 60 字) 去重，保留最高分
    best = {}
    for h in hits:
        key = (h["source"], h["text"][:60])
        if key not in best or h["score"] > best[key]["score"]:
            best[key] = h
    ranked = sorted(best.values(), key=lambda x: -(x.get("score") or 0))
    return ranked[:limit], modes


def _build_tool_steps(tool_calls_log):
    """ReAct 轨迹：只留前端要用的字段，preview 裁到 400 字符。

    不裁的话单个响应体里会塞进多次工具返回的原始 JSON，
    叠加图表 base64 之后接口体积会翻几倍。
    """
    steps = []
    for tc in tool_calls_log:
        parsed = tc.get("result_parsed")
        ok = bool(parsed.get("success")) if isinstance(parsed, dict) else None
        name = tc.get("tool") or ""
        steps.append({
            "iteration": tc.get("iteration"),
            "tool": name,
            "tool_cn": TOOL_CN.get(name, name),
            "arguments": tc.get("arguments", {}),
            "dup": bool(tc.get("dup")),
            "ok": ok,
            "preview": (tc.get("result_preview") or "")[:400],
        })
    return steps


def _converge_score(reason):
    """收敛原因 → 质量分。取值见 agent/engine.py::_build_metrics 的注释。"""
    if not reason:
        return 0.5
    if reason.startswith("force_answer:"):
        return {"no_new_info": 0.70, "soft_limit": 0.55}.get(
            reason.split(":", 1)[1], 0.50)
    return {
        "final_answer": 1.00,
        "force_answer_tool_calls": 0.50,
        "max_iterations": 0.35,
        "llm_error": 0.00,
        "empty_response": 0.00,
    }.get(reason, 0.50)


def _build_evidence_score(*, has_data, converged_reason, tool_calls_log, metrics):
    """「证据完备度」四分量加权 —— 替代原来硬编码的 `confidence: 0.85`。

    为什么不叫「置信度」：这四项衡量的是「本次回答的证据有多完整、链路有
    多干净」，不是「答案有多大概率正确」——后者需要标注集才能估计，本系统
    没有做（见论文局限）。挂一个没有算法的 0.85 在界面上，答辩被问
    「怎么算的」当场答不上来。

    每一项都能指到具体日志字段：
      数据到达 40%  sql / rag / report 是否真的产出内容
      收敛质量 30%  metrics.converged_reason
      链路干净 20%  1 - dedup_hits / tool_calls_total
      审计通过 10%  metrics.audit_status（未触发审计时该项不参与、权重归一化）
    """
    m = metrics or {}
    parts = [
        ("数据到达", 1.0 if has_data else 0.0, 0.40,
         "有 SQL 结果 / 研报命中 / 报告正文" if has_data else "本轮无任何数据产出"),
        ("收敛质量", _converge_score(converged_reason), 0.30,
         f"converged_reason = {converged_reason or '未知'}"),
    ]

    total = m.get("tool_calls_total") or len(tool_calls_log)
    dup = m.get("dedup_hits")
    if dup is None:
        dup = sum(1 for tc in tool_calls_log if tc.get("dup"))
    clean = 1.0 if not total else max(0.0, 1.0 - dup / total)
    parts.append(("链路干净", clean, 0.20, f"{total} 次调用 / {dup} 次被去重拦截"))

    audit = m.get("audit_status", "n/a")
    if audit != "n/a":
        # 单意图问题不经过 CoverageAuditor，n/a 时把这一档剔除、剩余权重归一化，
        # 否则「没触发审计」会被当成「审计只给一半分」。
        parts.append(("审计通过", {
            "passed": 1.0, "bounced": 0.70,
            "force_not_bounced": 0.50, "skipped": 0.50, "error": 0.30,
        }.get(audit, 0.50), 0.10, f"audit_status = {audit}"))

    weight = sum(w for _, _, w, _ in parts)
    score = sum(v * w for _, v, w, _ in parts) / weight if weight else 0.0
    basis = [{"name": n, "score": round(v, 2), "weight": w, "detail": d}
             for n, v, w, d in parts]
    return round(score, 3), basis


def _infer_run_status(*, source, reason, has_data, forced, missing):
    """运行状态四态，对应设计稿 03 画板。返回 (状态, 说明)。

    ok / partial / empty / max_iterations / error。
    现有页面只有「未连接 | 已连接」两档，用户在「没查到」和「系统坏了」
    之间无法区分；这里把 data_available、force_answer、覆盖审计这些
    已有机制映射成界面上看得见的状态。
    """
    if source == "error" or reason in ("llm_error", "empty_response"):
        return "error", "链路异常，结果可能不完整"
    if reason == "max_iterations" or source == "max_iterations":
        return "max_iterations", "已达最大轮数，返回当前已确认的结果"
    if not has_data:
        return "empty", "该口径在库中未命中记录 —— 属数据缺口，不是系统故障"
    note = ""
    if forced:
        note = f"已达收敛条件强制收口（{reason or 'force_answer'}）"
    if missing:
        note = (note + "；" if note else "") + f"覆盖审计标记 {missing} 项未答"
    if note:
        return "partial", note
    return "ok", ""



# ===== 核心处理 =====
def process_question(question, session_id="default"):
    try:
        t0 = time.time()   # elapsed_s：前端状态区要显示本轮耗时
        agent = get_agent()
        from agent.session import get_session_manager, extract_understanding_from_result

        session_mgr = get_session_manager()
        session = session_mgr.get_or_create(session_id)

        # 获取上一轮上下文（替代 FollowupResolver 关键词匹配）
        last_context = session.get_context_for_llm() if session.history else None
        # 轮数走 config.yaml `agent.conversation_history_turns`（原硬编码 3）
        history = session.get_history_for_llm()

        result = agent.run(
            question,
            history=history,
            session_id=session_id,
            last_context=last_context,
        )

        # 记录本轮（B3: 从本轮 tool 调用解析 company/indicator/year 槽位并回填会话，
        # 替代 {"from": "react"} 占位，保证下一轮"那营收呢"这类追问能继承上轮上下文）
        answer = result.answer or "暂无数据"
        understanding = extract_understanding_from_result(result)
        if any(understanding.values()):
            session.update_understanding(understanding)
        session.add_turn(
            question, answer, dict(session.slots),
            data=result.sql_data,
            task_type=result.source,
        )

        # ---- 从 tool_calls_log 提取展示所需数据 ----
        sql_data = result.sql_data
        chart_html = result.chart_html
        metrics = result.metrics or {}
        report_md = None
        risk_alerts = None
        sql_text = ""
        tool_companies, tool_indicators, tool_years = [], [], []

        for tc in result.tool_calls_log:
            parsed = tc.get("result_parsed", {})
            if not isinstance(parsed, dict):
                continue
            tool = tc.get("tool")
            if tool == "report" and parsed.get("report") and not report_md:
                report_md = parsed["report"]
            if tool == "risk_analysis" and parsed.get("alerts") and risk_alerts is None:
                risk_alerts = parsed["alerts"]
            # 2026-09-17：SQL 语句与口径槽位本来就躺在 sql_tool 的返回体里
            # （sql / companies / indicators / years / unit），此前这里只取
            # data 一个键，其余全丢 → 前端「SQL 溯源」页签永不出现、
            # 结果元信息行永远是死的。dup 的重试记录不覆盖首次结果。
            if tool == "sql_query" and not tc.get("dup"):
                if parsed.get("sql"):
                    sql_text = parsed["sql"]
                if parsed.get("companies"):
                    tool_companies = list(parsed["companies"])
                if parsed.get("indicators"):
                    tool_indicators = list(parsed["indicators"])
                if parsed.get("years"):
                    tool_years = list(parsed["years"])

        # ---- 研报命中（结构化，供「研报参考」页签渲染）----
        rag_hits, rag_modes = _collect_rag_hits(result.tool_calls_log)

        # ---- 结论与明细并列，不再二选一 ----
        # 原实现 `if report_md / elif sql_data` + 前端 `answer || display_html`
        # 双重短路，而 answer 恒非空 → 两个渲染函数都是死代码。
        display_html = ""
        display_kind = ""
        if report_md:
            display_html = build_report_html(report_md)
            display_kind = "report"
        elif sql_data:
            display_html = build_html_table(sql_data)
            display_kind = "table"

        # ---- 风险预警：保留旧字段（字符串）+ 新增结构化列表 ----
        risk_html = None
        if risk_alerts:
            parts = ['<div class="risk-section"><b>风险预警</b>']
            for a in risk_alerts:
                sev_class = {"高": "high", "中": "mid", "低": "low"}.get(
                    a.get("severity", "低"), "low")
                parts.append(
                    f'<div class="risk-item {sev_class}">'
                    f'<span class="risk-sev">[{_esc(a.get("severity", ""))}]</span> '
                    f'{_esc(a.get("rule", ""))}：{_esc(a.get("detail", ""))}</div>'
                )
            parts.append('</div>')
            risk_html = "".join(parts)

        # ---- route：本次实际走过的工具链（中文名去重保序）----
        route_names = []
        for tc in result.tool_calls_log:
            name = tc.get("tool") or ""
            cn = TOOL_CN.get(name, name)
            if cn and cn not in route_names:
                route_names.append(cn)

        # ---- 口径：期间 / 公司 ----
        # 结果集可能跨多个报告期。2026-09-17 实测「中药行业研发投入现状分析」
        # 返回 670 行，首行是 2023Q1、数值最大行是 2025Q3 —— 只报首行的期间
        # 会与 KPI 卡上的期间互相打架，所以先收集去重，只有唯一期间时才敢报。
        periods = []
        for row in (sql_data or []):
            label = _period_label(row)
            if label and label not in periods:
                periods.append(label)
        if len(periods) == 1:
            period = periods[0]
            period_note = _period_note(period)
        elif periods:
            period = ""
            period_note = "结果跨 {} 个报告期（{} … {}），未按单期筛选".format(
                len(periods), periods[0], periods[-1])
        else:
            period = "、".join(str(y) for y in tool_years) if tool_years else ""
            period_note = _period_note(period)
        company = "、".join(str(c) for c in tool_companies) if tool_companies else ""

        # ---- 运行状态 + 证据完备度 ----
        has_data = bool(sql_data) or bool(rag_hits) or bool(report_md)
        forced = bool(metrics.get("force_answer_triggered"))
        run_status, run_note = _infer_run_status(
            source=result.source,
            reason=metrics.get("converged_reason", ""),
            has_data=has_data,
            forced=forced,
            missing=metrics.get("audit_missing_count") or 0,
        )
        confidence, confidence_basis = _build_evidence_score(
            has_data=has_data,
            converged_reason=metrics.get("converged_reason", ""),
            tool_calls_log=result.tool_calls_log,
            metrics=metrics,
        )

        return {
            "question": question,
            # --- 兼容字段：前端 v2 已在读，勿删 ---
            "task_type": result.source,
            "intent": result.source,
            "plan": " → ".join(tc["tool"] for tc in result.tool_calls_log),
            "answer": answer,
            "display_html": display_html,
            # 前端据此决定明细区的呈现：table 内联展开；report 收进折叠区
            # （report 正文常与 answer 高度重叠，内联会重复渲染同一段内容）
            "display_kind": display_kind,
            "rag_html_raw": risk_html,
            "chart": chart_html,
            "result_count": len(sql_data) if sql_data else 0,
            "trace": result.trace,
            # --- 2026-09-17 补齐：前端一直在要、后端从来没给过的字段 ---
            "confidence": confidence,          # 不再是硬编码 0.85
            "confidence_basis": confidence_basis,
            "route": " → ".join(route_names),
            "sql": sql_text,
            "has_rag": bool(rag_hits),
            "rag_hits": rag_hits,
            "rag_modes": rag_modes or {},
            "risk_alerts": risk_alerts or [],
            "company": company,
            "result_period": period,
            "period_note": period_note,
            "indicators": tool_indicators,
            "kpi": _build_kpi(sql_data, tool_indicators, question),
            "iterations": result.iterations,
            "tool_steps": _build_tool_steps(result.tool_calls_log),
            "metrics": metrics,
            "run_status": run_status,
            "run_status_note": run_note,
            "elapsed_s": round(time.time() - t0, 2),
        }

    except Exception as e:
        import traceback
        print(f"[ERROR] process_question failed: {e}")
        traceback.print_exc()
        # 异常路径同样返回完整键集：前端 v2 直接用 data.rag_hits.length 这类
        # 取值，缺键会抛 TypeError 被 catch 吞掉，界面上只看到空白、查不出原因。
        return {
            "question": question,
            "task_type": "error",
            "intent": "error",
            "plan": "",
            "confidence": 0.0,
            "confidence_basis": [],
            "answer": f"处理失败: {str(e)}",
            "display_html": "",
            "display_kind": "",
            "rag_html_raw": None,
            "chart": None,
            "result_count": 0,
            "route": "",
            "sql": "",
            "has_rag": False,
            "rag_hits": [],
            "rag_modes": {},
            "risk_alerts": [],
            "company": "",
            "result_period": "",
            "period_note": "",
            "indicators": [],
            "kpi": {"cards": []},
            "iterations": 0,
            "tool_steps": [],
            "metrics": {},
            "run_status": "error",
            "run_status_note": "后端处理异常，详见服务端日志",
            "elapsed_s": 0.0,
        }


# ===== Flask 路由 =====
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/query", methods=["POST"])
def query():
    d = request.get_json()
    q = d.get("question", "").strip()
    if not q:
        return jsonify({"error": "empty"}), 400
    session_id = _resolve_session_id(d)
    resp = jsonify(process_question(q, session_id=session_id))
    resp.set_cookie("sid", session_id, max_age=1800)
    return resp


@app.route("/api/debug", methods=["POST"])
def debug_query():
    """诊断端点：展示 ReAct 追踪链路"""
    d = request.get_json()
    q = d.get("question", "").strip()
    if not q:
        return jsonify({"error": "empty"}), 400

    agent = get_agent()
    from agent.tracer import get_tracer

    result = agent.run(q, session_id="debug")
    tracer = get_tracer()

    return jsonify({
        "question": q,
        "answer": result.answer,
        "iterations": result.iterations,
        "source": result.source,
        "tool_calls": [
            {
                "iteration": tc["iteration"],
                "tool": tc["tool"],
                "arguments": tc.get("arguments", {}),
                "preview": tc.get("result_preview", "")[:200],
            }
            for tc in result.tool_calls_log
        ],
        "trace_tree": tracer.render_console(),
    })


@app.route("/api/reset", methods=["POST"])
def reset():
    from agent.session import get_session_manager
    session_id = _resolve_session_id(request.get_json())
    get_session_manager().reset(session_id)
    return jsonify({"status": "ok"})


@app.route("/api/health")
def health():
    """轻量健康检查：MySQL 与向量检索服务（Ollama）是否在线。

    页面原来只有「未连接 / 已连接」两档，且「已连接」只反映上一次问答请求
    是否成功 —— 加载中、后台服务挂了都会显示「已连接」。这里给出两个真实
    可探的依赖状态，让用户能把「查不到」区分成「数据缺口」还是「依赖没起」。
    """
    import socket
    out = {"mysql": False, "rag": False, "mysql_detail": "", "rag_detail": ""}

    # MySQL：真实建连 + SELECT 1，比探端口可信（端口通不等于库能查）
    try:
        import mysql.connector
        from agent.config import get_database_config
        cfg = get_database_config()
        conn = mysql.connector.connect(
            host=cfg["host"], port=int(cfg["port"]), database=cfg["database"],
            user=cfg["user"], password=cfg["password"], connection_timeout=3)
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.close()
            out["mysql"] = True
            out["mysql_detail"] = f"{cfg['database']} @ {cfg['host']}:{cfg['port']}"
        finally:
            conn.close()
    except Exception as e:
        out["mysql_detail"] = str(e)[:120]

    # Ollama：RAG 向量化的硬依赖。只探端口，不拉模型（拉模型要数十秒）
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=3):
            out["rag"] = True
        out["rag_detail"] = "Ollama :11434（bge-m3）"
    except Exception as e:
        out["rag_detail"] = f"不可达：{str(e)[:80]}"

    return jsonify(out)


@app.route("/api/quality")
def quality_api():
    """返回数据质量报告 JSON"""
    from data_quality_report import generate_report
    return jsonify(generate_report())


@app.route("/quality")
def quality_dashboard():
    """数据质量看板 HTML 页面"""
    from data_quality_report import generate_report, format_html
    report = generate_report()
    return format_html(report)


def _kill_existing_on_port(port: int) -> None:
    """结束当前监听指定端口的进程（本机开发用：防止旧进程占端口）。

    用 netstat + taskkill 实现，不引入 psutil 等额外依赖。
    """
    try:
        out = subprocess.check_output(["netstat", "-ano"], timeout=15)
    except Exception as e:
        print(f"[app] 端口清理：netstat 调用失败，跳过（{e}）")
        return

    pids = set()
    for line in out.decode("ascii", errors="ignore").splitlines():
        if (f":{port}" not in line) or ("LISTENING" not in line):
            continue
        parts = line.split()
        if parts and parts[-1].isdigit():
            pids.add(int(parts[-1]))

    cur = os.getpid()
    for pid in pids:
        if pid == cur:
            continue
        print(f"[app] 端口清理：结束占用 {port} 的旧进程 PID={pid}")
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=15)
        except Exception as e:
            print(f"[app] 端口清理：结束 PID={pid} 失败（{e}）")


if __name__ == "__main__":
    # 2026-09 接线：主机/端口/调试开关改读 config.yaml `server.*`。
    # 此前 app.py 硬编码 127.0.0.1:7860，而 agent/config.py 早已把环境变量
    # PORT / DEBUG 映射到 server.port / server.debug —— 属“配置写了没人读”
    # 的孤儿项（答辩问“端口能改吗”会答不上来）。默认值与原硬编码一致。
    _host, _port, _debug = "127.0.0.1", 7860, False
    try:
        from agent.config import get_config
        _cfg = get_config()
        _cfg.load()
        _host = _cfg.get("server.host", _host) or _host
        _port = int(_cfg.get("server.port", _port) or _port)
        _debug = bool(_cfg.get("server.debug", _debug))
    except Exception as e:
        print(f"[app] 读取 server 配置失败，回退默认 {_host}:{_port}: {e}")

    print("=" * 50)
    print("  ReAct Agent 财务问答系统")
    print(f"  问答页面: http://{_host}:{_port}")
    print(f"  质量看板: http://{_host}:{_port}/quality")
    print("=" * 50)

    # 每次启动前先清掉同端口旧进程，避免“改了代码旧进程还占着 7860”。
    # DEBUG 重载模式下，Werkzeug 会重跑本脚本，只在主进程清一次即可。
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        _kill_existing_on_port(_port)

    app.run(host=_host, port=_port, debug=_debug)
