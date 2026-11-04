/* ============================================================
   智能问数 — 前端交互 v3
   2026-09-17 重写。契约见 app.py::process_question。

   v2 的坑（本次全部修掉）：
     · has_rag / sql / route / result_period 后端从来没给过，
       「研报参考」「SQL 溯源」两个页签永不出现，置信度后面永远空括号
     · confidence 后端硬编码 0.85 → 现在展示的是可分解的「证据完备度」
     · 「清空」只清 DOM，不调 /api/reset → 追问仍继承上一轮上下文
     · 状态只有「已连接 / 未连接」，且只反映上一次请求是否成功
   ============================================================ */

(function () {
    'use strict';

    // ============================== 常量 ==============================

    var QUICK = [
        { tag: '排名', q: '2025年Q3营收排名前五的中药企业', t: '2025Q3营收前五' },
        { tag: '趋势', q: '华润三九近三年利润总额变化趋势', t: '三年利润趋势' },
        { tag: '对比', q: '白云山与云南白药2024年盈利能力对比', t: '盈利能力对比' },
        { tag: '分析', q: '中药行业研发投入现状分析', t: '研发投入分析' },
        { tag: '归因', q: '同仁堂资产负债率偏高的原因', t: '负债率归因' },
        { tag: '查询', q: '金花股份2024年净利润', t: '金花净利润' }
    ];

    var WELCOME = [
        { t: '营收排行', q: '2024年营收排名前十的中药企业' },
        { t: '盈利趋势', q: '华润三九近三年利润变化趋势' },
        { t: '财务健康', q: '资产负债率低于40%的中药企业有哪些' },
        { t: '行业均值', q: '中药行业平均研发费用率' }
    ];

    var INTENT_CN = {
        basic_query: '财务报表', query: '财务报表', stat_query: '统计分析',
        comparison: '企业对比', time_trend: '趋势分析', fuzzy_intent: '模糊查询',
        open_question: '研报解读', analysis_query: '归因分析', analysis: '归因分析',
        risk: '风险预警', report: '财务报告', trend: '趋势分析', compare: '企业对比',
        agent: '多工具协同', fallback: '降级应答', max_iterations: '迭代收敛',
        error: '处理异常'
    };

    // run_status → 徽标文案与配色（对应设计稿 03 画板）
    var STATUS_CN = {
        ok: { text: '已核验', cls: 'ok' },
        partial: { text: '部分收口', cls: 'warn' },
        empty: { text: '无命中', cls: 'amber' },
        max_iterations: { text: '迭代超限', cls: 'danger' },
        error: { text: '异常', cls: 'danger' }
    };

    // ============================== DOM ==============================

    var $ = function (id) { return document.getElementById(id); };
    var S = {
        input: $('input'),
        send: $('send'),
        chat: $('chat'),
        content: $('content'),
        welcome: $('welcome'),
        quickList: $('quickList'),
        welcomeBtns: $('welcomeBtns'),
        convTitle: $('convTitle'),
        convMeta: $('convMeta'),
        statusPill: $('statusPill'),
        statusDot: $('statusDot'),
        statusText: $('statusText'),
        clearBtn: $('clearBtn'),
        lightbox: $('lightbox'),
        lightboxImg: $('lightboxImg'),
        sidebar: $('sidebar'),
        drawerBtn: $('drawerBtn'),
        drawerMask: $('drawerMask')
    };

    var busy = false;
    var currentAbort = null;   // 当前请求的 AbortController（第三批 ⑬ 停止生成）
    var turns = 0;
    var lastContext = null;
    var firstQuestion = true;

    // ============================== 工具函数 ==============================

    function esc(value) {
        var d = document.createElement('div');
        d.textContent = (value === null || value === undefined) ? '' : String(value);
        return d.innerHTML;
    }

    function el(html) {
        var tpl = document.createElement('template');
        tpl.innerHTML = String(html).trim();
        return tpl.content.firstElementChild;
    }

    /** 行内标记：**加粗** / `代码` / *斜体*。先转义再替换。
     *  斜体要求首尾都是非空白非星号字符，避免「营收 * 利润率 * 系数」
     *  这种孤星被误配成斜体。 */
    function inline(text) {
        return esc(text)
            .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
            .replace(/`(.+?)`/g, '<code>$1</code>')
            .replace(/\*([^\s*](?:[^*\n]*[^\s*])?)\*/g, '<em>$1</em>');
    }

    /** 单元格涨跌方向 → 'up' | 'down' | ''（第三批 ⑫）。
     *
     *  只认「整格内容就是一个带**显式符号**的数或百分数」：+4.3% / -50.6% / ＋12。
     *  裸数字一律不认 —— 年份(2025)、序号(1)、金额(6160599.38)、股数(1.2亿)
     *  都没有方向语义，染红染绿会把表格变成红绿灯。
     *  单个横杠（"-" 表示无数据）也不认：符号后必须紧跟数字。
     *  全角 ＋－ 一并认，LLM 偶尔输出全角。
     *
     *  语义假设：正=涨(红)、负=跌(绿)，中国习惯。财务语境下「同比 +4.3%」= 增长，
     *  方向与符号一致；但「成本降幅 -50.6%」这类**负数其实是好事**的场景仍会被染绿，
     *  属已知边界 —— 列名级语义判断需要表头解析，本批不做（见 §12 ⑦）。 */
    function numDir(raw) {
        var t = String(raw)
            .replace(/\*\*/g, '').replace(/`/g, '').replace(/\*/g, '')
            .replace(/[\s,，]/g, '');          // 剥行内标记 + 千分位
        if (/^[+＋][\d.]/.test(t)) return 'up';
        if (/^[-−－][\d.]/.test(t)) return 'down';
        return '';
    }

    /** 极简 Markdown → HTML。逐行状态机，不做 `<br>` 拼接（会污染表格）。
     *  2026-09-17 补齐：有序列表 / 分隔线 / 引用；表格套 .table-wrap 使其可横向滚动。 */
    function renderMarkdown(md) {
        if (!md) return '';
        var lines = String(md).split('\n');
        var out = [];
        var inTable = false;
        var listTag = '';   // '' | 'ul' | 'ol'

        function closeTable() {
            if (inTable) { out.push('</tbody></table></div>'); inTable = false; }
        }
        function closeList() {
            if (listTag) { out.push('</' + listTag + '>'); listTag = ''; }
        }

        for (var i = 0; i < lines.length; i++) {
            var t = lines[i].trim();

            // 表格行。外层 .table-wrap 提供 overflow-x —— 少了它，
            // 8 列以上的表会把内容区撑到视口外且无法滚动查看。
            if (t.charAt(0) === '|' && t.charAt(t.length - 1) === '|') {
                closeList();
                var cells = t.replace(/^\||\|$/g, '').split('|');
                var sep = true;
                for (var c = 0; c < cells.length; c++) {
                    if (!/^[-: ]*$/.test(cells[c].trim())) { sep = false; break; }
                }
                if (sep) continue;
                if (!inTable) {
                    out.push('<div class="table-wrap"><table class="md-table"><tbody>');
                    inTable = true;
                }
                out.push('<tr>');
                for (var k = 0; k < cells.length; k++) {
                    var cell = cells[k].trim();
                    // 方向用原始文本判（inline() 之后带标签，正则会失配）
                    var dir = numDir(cell);
                    out.push('<td' + (dir ? ' class="num-' + dir + '"' : '') + '>'
                        + inline(cell) + '</td>');
                }
                out.push('</tr>');
                continue;
            }
            closeTable();

            if (!t) { closeList(); continue; }

            // 分隔线 --- / *** / ___（原先漏成正文，用户看到一行横杠）
            if (/^(?:-{3,}|\*{3,}|_{3,})$/.test(t)) {
                closeList();
                out.push('<hr class="md-hr">');
                continue;
            }
            // 引用行 > 开头（原先漏成正文，还带着一个 `&gt;`）
            if (t.charAt(0) === '>') {
                closeList();
                out.push('<blockquote class="md-quote">'
                    + inline(t.replace(/^>\s?/, '')) + '</blockquote>');
                continue;
            }

            var h = t.match(/^(#{1,6})\s+(.*)$/);
            if (h) {
                closeList();
                var lv = Math.min(h[1].length + 1, 6);
                out.push('<h' + lv + '>' + inline(h[2]) + '</h' + lv + '>');
                continue;
            }
            if (/^[-*+]\s+/.test(t)) {
                if (listTag !== 'ul') { closeList(); out.push('<ul>'); listTag = 'ul'; }
                out.push('<li>' + inline(t.replace(/^[-*+]\s+/, '')) + '</li>');
                continue;
            }
            // 有序列表 1. / 1、/ 1) —— 研报式回答里编号段落极常见，
            // 原先整段落进 <p>，序号和内容挤成一行读不出列表结构。
            if (/^\d+[.、)]\s+/.test(t)) {
                if (listTag !== 'ol') { closeList(); out.push('<ol>'); listTag = 'ol'; }
                out.push('<li>' + inline(t.replace(/^\d+[.、)]\s+/, '')) + '</li>');
                continue;
            }
            closeList();
            out.push('<p>' + inline(t) + '</p>');
        }
        closeTable();
        closeList();
        return out.join('');
    }

    /** answer 里是否已经带了表格 —— 带了就不再内联 display_html，避免同一份数据渲染两遍。 */
    function hasTable(md) {
        var lines = String(md || '').split('\n');
        for (var i = 0; i < lines.length; i++) {
            var t = lines[i].trim();
            if (t.charAt(0) === '|' && t.charAt(t.length - 1) === '|') return true;
        }
        return false;
    }

    function argsBrief(args) {
        if (!args || typeof args !== 'object') return '';
        var parts = [];
        var keys = Object.keys(args);
        for (var i = 0; i < keys.length && i < 4; i++) {
            var v = args[keys[i]];
            if (v === null || v === undefined || v === '') continue;
            if (typeof v === 'object') v = JSON.stringify(v);
            v = String(v);
            if (v.length > 40) v = v.slice(0, 40) + '…';
            parts.push(keys[i] + '=' + v);
        }
        return parts.join(' · ');
    }

    // ============================== 回答渲染 ==============================

    function renderHead(data) {
        var st = STATUS_CN[data.run_status] || STATUS_CN.ok;
        var intent = INTENT_CN[data.intent] || INTENT_CN[data.task_type] || '查询';

        var meta = [];
        if (data.iterations) meta.push(data.iterations + ' 轮迭代');
        var m = data.metrics || {};
        if (m.tool_calls_total) meta.push(m.tool_calls_total + ' 次工具调用');
        if (m.dedup_hits) meta.push(m.dedup_hits + ' 次重复被拦');
        if (data.elapsed_s) meta.push(data.elapsed_s + 's');
        if (data.result_period) meta.push(data.result_period);
        if (data.result_count) meta.push(data.result_count + ' 条结果');

        var box = el('<div class="ans-head"></div>');
        box.appendChild(el('<span class="badge badge-intent">' + esc(intent) + '</span>'));
        if (data.route) {
            box.appendChild(el('<span class="badge badge-tool">' + esc(data.route) + '</span>'));
        }
        box.appendChild(el('<span class="badge badge-status ' + st.cls + '">'
            + esc(st.text) + '</span>'));
        if (meta.length) {
            box.appendChild(el('<span class="ans-meta">' + esc(meta.join(' · ')) + '</span>'));
        }
        return box;
    }

    function renderKpi(kpi) {
        var cards = (kpi && kpi.cards) || [];
        if (!cards.length) {
            // 后端在「结果跨多个报告期」时会主动不出卡（合计无业务含义）。
            // 说明原因，避免看起来像功能坏了。
            if (kpi && kpi.skipped) {
                return el('<p class="kpi-skipped">未生成汇总指标：'
                    + esc(kpi.skipped) + '</p>');
            }
            return null;
        }
        var row = el('<div class="kpi-row"></div>');
        for (var i = 0; i < cards.length; i++) {
            var c = cards[i];
            row.appendChild(el(
                '<div class="kpi-card">'
                + '<p class="kpi-label">' + esc(c.label) + '</p>'
                + '<p class="kpi-value"><span class="kpi-num">' + esc(c.value)
                + '</span><span class="kpi-unit">' + esc(c.unit) + '</span></p>'
                + '<p class="kpi-note">' + esc(c.note) + '</p>'
                + '</div>'));
        }
        return row;
    }

    function renderRisks(alerts) {
        if (!alerts || !alerts.length) return null;
        var box = el('<div class="risk-section"></div>');
        box.appendChild(el('<p class="risk-title">风险预警 · ' + alerts.length + ' 条</p>'));
        var map = { '高': 'high', '中': 'mid', '低': 'low' };
        for (var i = 0; i < alerts.length; i++) {
            var a = alerts[i];
            var cls = map[a.severity] || 'low';
            box.appendChild(el(
                '<div class="risk-item ' + cls + '">'
                + '<span class="risk-sev">' + esc(a.severity || '') + '</span>'
                + '<span><span class="risk-rule">' + esc(a.rule || '') + '</span>：'
                + esc(a.detail || '') + '</span>'
                + '</div>'));
        }
        return box;
    }

    function buildEvidence(data, detailHtml) {
        var hits = data.rag_hits || [];
        var steps = data.tool_steps || [];
        var sql = data.sql || '';
        var detail = detailHtml || '';
        if (!sql && !hits.length && !steps.length && !detail) return null;

        var counts = [];
        if (sql) counts.push('SQL 1 条');
        if (hits.length) counts.push('研报 ' + hits.length + ' 条');
        if (steps.length) counts.push('ReAct ' + steps.length + ' 步');
        if (detail) counts.push('明细 ' + detailRows(detail) + ' 行');

        var det = el('<details class="evidence"></details>');
        det.appendChild(el(
            '<summary>'
            + '<svg class="chev" width="16" height="16" viewBox="0 0 16 16" fill="none">'
            + '<path d="M6 4l4 4-4 4" stroke="#5D6C7B" stroke-width="1.8" '
            + 'stroke-linecap="round" stroke-linejoin="round"/></svg>'
            + '<span class="ev-title">证据链</span>'
            + '<span class="ev-count">' + esc(counts.join(' · ')) + '</span>'
            + '<span class="ev-hint">展开可核对数字来源</span>'
            + '</summary>'));

        var body = el('<div class="ev-body"></div>');

        // ---- 页签 ----
        var tabs = el('<div class="ev-tabs"></div>');
        var panels = el('<div></div>');
        var order = [];
        if (detail) order.push('detail');
        if (sql) order.push('sql');
        if (hits.length) order.push('rag');
        if (steps.length) order.push('react');

        var labels = {
            detail: '结构化明细 ' + detailRows(detail) + ' 行',
            sql: 'SQL 溯源',
            rag: '研报参考 ' + hits.length,
            react: '推理轨迹 ' + steps.length + ' 步'
        };
        for (var i = 0; i < order.length; i++) {
            var name = order[i];
            var tab = el('<button type="button" class="ev-tab'
                + (i === 0 ? ' is-on' : '') + '" data-tab="' + name + '">'
                + esc(labels[name]) + '</button>');
            tabs.appendChild(tab);

            var panel = el('<div class="ev-panel" data-panel="' + name + '"'
                + (i === 0 ? '' : ' hidden') + '></div>');
            if (name === 'detail') panel.appendChild(buildDetailPanel(detail));
            if (name === 'sql') panel.appendChild(buildSqlPanel(data));
            if (name === 'rag') panel.appendChild(buildRagPanel(data));
            if (name === 'react') panel.appendChild(buildStepsPanel(steps));
            panels.appendChild(panel);
        }
        body.appendChild(tabs);
        body.appendChild(panels);
        // 走过 SQL 却拿不到语句（pipeline 兜底路径不回传 sql）。
        // 不说清楚的话，「工具链」徽标写着 SQL 查库、证据链里却没有 SQL，看着像漏了。
        if (!sql && String(data.route || '').indexOf('SQL') >= 0) {
            body.appendChild(el('<p class="sql-label" style="margin-top:10px">'
                + '本次 SQL 由 pipeline 兜底路径执行，工具未回传语句，无法展示溯源原文</p>'));
        }
        det.appendChild(body);
        return det;
    }

    /** display_html 自带一个 .result-table-wrap（无 CSS 的死包），这里只数数据行供页签标签用。 */
    function detailRows(html) {
        var all = (String(html || '').match(/<tr[\s>]/g) || []).length;
        return /<thead/i.test(html) ? Math.max(all - 1, 0) : all;
    }

    /** 后端确定性明细表。收进证据链是为了随时可核对，而不抢正文的叙述位。 */
    function buildDetailPanel(html) {
        var box = el('<div></div>');
        box.appendChild(el('<p class="sql-label">结构化明细 · 由后端按指标知识层的列名与单位换算生成，'
            + '与「SQL 溯源」的返回行一一对应。正文里的表格由模型自拟，列名与行数可能不同；'
            + '两处数字冲突时以本表为准</p>'));
        box.appendChild(el('<div class="table-wrap">' + html + '</div>'));
        return box;
    }

    function buildSqlPanel(data) {
        var box = el('<div class="sql-block"></div>');
        var note = '执行的 SQL · 由 sql_query 生成并执行';
        if (data.result_count) note += '，返回 ' + data.result_count + ' 行';
        if (data.metrics && data.metrics.force_answer_reason) {
            note += ' · 收敛原因 ' + data.metrics.force_answer_reason;
        }
        box.appendChild(el('<p class="sql-label">' + esc(note) + '</p>'));
        box.appendChild(el('<pre class="sql-code">' + esc(data.sql) + '</pre>'));
        return box;
    }

    function buildRagPanel(data) {
        var box = el('<div></div>');
        var modes = data.rag_modes || {};
        var tips = [];
        if (modes.rerank_mode && modes.rerank_mode !== 'off') {
            tips.push('精排 ' + modes.rerank_mode);
        }
        if (modes.company_mode && modes.company_mode !== 'off') {
            tips.push('company ' + modes.company_mode);
        }
        box.appendChild(el('<p class="sql-label">命中片段 · 分数为 RRF 融合分'
            + '（FAISS + BM25），不是概率，仅用于排序比较'
            + (tips.length ? ' · ' + esc(tips.join(' · ')) : '') + '</p>'));
        for (var i = 0; i < data.rag_hits.length; i++) {
            var h = data.rag_hits[i];
            var meta = [];
            if (h.company) meta.push(h.company);
            if (h.section) meta.push(h.section);
            if (h.year) meta.push(h.year);
            var score = (typeof h.score === 'number') ? h.score.toFixed(3) : h.score;
            box.appendChild(el(
                '<div class="rag-item">'
                + '<div class="rag-head">'
                + '<span class="rag-src">' + esc(h.source || '未标注来源') + '</span>'
                + '<span class="rag-score">第 ' + (i + 1) + ' 位 · 融合分 ' + esc(score) + '</span>'
                + '</div>'
                + (meta.length ? '<p class="rag-meta">' + esc(meta.join(' · ')) + '</p>' : '')
                + '<p class="rag-text">' + esc(String(h.text || '').slice(0, 260)) + '…</p>'
                + '</div>'));
        }
        return box;
    }

    function buildStepsPanel(steps) {
        var box = el('<div class="steps"></div>');
        for (var i = 0; i < steps.length; i++) {
            var s = steps[i];
            var tag, cls;
            if (s.dup) { tag = '已去重'; cls = 'dup'; }
            else if (s.ok === false) { tag = '失败'; cls = 'bad'; }
            else { tag = '成功'; cls = 'ok'; }
            var brief = argsBrief(s.arguments);
            // 终端式排布：`[工具id] 中文名 参数`。
            // 工具 id 用等宽方括号标注，是为了让「这一步跑了哪个工具」一眼可辨 ——
            // 证据链正是论文卖点（多步工具调用），工具名不该被中文名盖住。
            // 中文名保留：评委未必认得 sql_query。
            var toolTag = s.tool ? '[' + esc(s.tool) + ']' : esc(s.tool_cn || '');
            var toolCn = (s.tool && s.tool_cn)
                ? '<span class="step-cn">' + esc(s.tool_cn) + '</span>'
                : '';
            box.appendChild(el(
                '<div class="step">'
                + '<span class="step-no">' + esc(s.iteration || (i + 1)) + '</span>'
                + '<span class="step-main">'
                + '<span class="step-tool">' + toolTag + '</span>'
                + toolCn
                + (brief ? '<p class="step-args">' + esc(brief) + '</p>' : '')
                + '</span>'
                + '<span class="step-tag ' + cls + '">' + tag + '</span>'
                + '</div>'));
        }
        return box;
    }

    function buildConfidence(data) {
        var basis = data.confidence_basis || [];
        if (!basis.length) return null;
        var pct = Math.round((data.confidence || 0) * 100);

        var wrap = el('<div></div>');

        // 满格时不出仪表条：只报「证据齐备 N/N」。
        // 恒 100% 的满宽绿条会把视线从答案上拉走，而它传达的信息量为零。
        var met = 0;
        for (var k = 0; k < basis.length; k++) {
            if ((basis[k].score || 0) >= 1) met++;
        }
        var row = (pct >= 100)
            ? el('<div class="conf-row is-full">'
                + '<span class="conf-ok">'
                + '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true">'
                + '<path d="M3 8.5l3.2 3.2L13 5" stroke="currentColor" stroke-width="2" '
                + 'stroke-linecap="round" stroke-linejoin="round"/></svg>'
                + '证据齐备 ' + met + '/' + basis.length + '</span>'
                + '<button type="button" class="conf-toggle">看算法</button>'
                + '</div>')
            : el('<div class="conf-row">'
                + '<span class="conf-label">证据完备度</span>'
                + '<span class="conf-bar conf-bar-warn"><i style="width:' + pct + '%"></i></span>'
                + '<span class="conf-num">' + pct + '%</span>'
                + '<button type="button" class="conf-toggle">看算法</button>'
                + '</div>');

        var panel = el('<div class="conf-panel" hidden></div>');
        for (var i = 0; i < basis.length; i++) {
            var b = basis[i];
            var bs = Math.round((b.score || 0) * 100);
            panel.appendChild(el(
                '<div class="conf-item' + (bs < 100 ? ' is-low' : '') + '">'
                + '<span class="conf-name">' + esc(b.name) + '</span>'
                + '<span class="conf-weight">权重 ' + Math.round((b.weight || 0) * 100) + '%</span>'
                + '<span class="conf-bar"><i style="width:' + bs + '%"></i></span>'
                + '<span class="conf-detail">' + esc(b.detail) + ' · 得分 ' + bs + '%</span>'
                + '</div>'));
        }
        panel.appendChild(el('<p class="conf-foot">这是「证据完备度」，衡量本次回答的证据完整度与链路干净程度，'
            + '不是「答案正确的概率」——后者需要人工标注集才能估计。'
            + '每个分量都对应一条运行日志，可在「推理轨迹」与「SQL 溯源」中逐项核对。</p>'));

        wrap.appendChild(row);
        wrap.appendChild(panel);
        return wrap;
    }

    function buildAnswer(data) {
        var box = el('<div></div>');
        box.appendChild(renderHead(data));

        // 口径提示：库里的 Q3/H1 是累计口径（年初至该期末），
        // 不写清楚用户会把「前三季度 616 亿」读成「单季 616 亿」。
        if (data.period_note) {
            box.appendChild(el('<p class="period-note">'
                + '<span class="period-tag">' + esc(data.result_period || '口径') + '</span>'
                + esc(data.period_note) + '</p>'));
        }

        var st = STATUS_CN[data.run_status] || STATUS_CN.ok;
        if (data.run_status !== 'ok' && data.run_status_note) {
            box.appendChild(el('<div class="status-note ' + st.cls + '">'
                + esc(data.run_status_note) + '</div>'));
        }

        var kpi = renderKpi(data.kpi);
        if (kpi) box.appendChild(kpi);

        var answer = data.answer || '';
        if (answer && answer !== '暂无数据') {
            box.appendChild(el('<div class="answer-text">' + renderMarkdown(answer) + '</div>'));
        }

        if (data.chart) {
            var cw = el('<div class="chart-wrap"></div>');
            cw.appendChild(el('<img src="data:image/png;base64,' + data.chart
                + '" alt="图表" loading="lazy">'));
            cw.appendChild(el('<p class="chart-foot">点击图表可放大 · '
                + '数值口径见下方证据链</p>'));
            box.appendChild(cw);
        }

        // 明细表：answer 没有表格时，后端这张表就是主表（确定性列名 + 单位换算）。
        // answer 自带表格时不重复渲染，但仍要保证它可达 —— 改为收进证据链的
        // 「结构化明细」页签。实测 12 个留档样本里它的上屏率只有 3/12（25%）：
        // LLM 几乎每次都自己写表，把后端 taxonomy + UnitConverter 的结果整体挤掉了。
        var detailHtml = '';
        if (data.display_html && data.display_kind === 'table') {
            if (hasTable(answer)) detailHtml = data.display_html;
            else box.appendChild(el('<div class="table-wrap">' + data.display_html + '</div>'));
        }

        var risks = renderRisks(data.risk_alerts);
        if (risks) box.appendChild(risks);

        // 报告型明细：正文常与 answer 高度重叠，收进折叠区
        if (data.display_html && data.display_kind === 'report') {
            var rep = el('<details class="evidence"></details>');
            rep.appendChild(el('<summary>'
                + '<svg class="chev" width="16" height="16" viewBox="0 0 16 16" fill="none">'
                + '<path d="M6 4l4 4-4 4" stroke="#5D6C7B" stroke-width="1.8" '
                + 'stroke-linecap="round" stroke-linejoin="round"/></svg>'
                + '<span class="ev-title">结构化报告</span>'
                + '<span class="ev-hint">按章节指标生成的完整报表</span>'
                + '</summary>'
                + '<div class="ev-body"><div class="report-wrap"></div></div>'));
            rep.querySelector('.report-wrap').innerHTML = data.display_html;
            box.appendChild(rep);
        }

        var ev = buildEvidence(data, detailHtml);
        if (ev) box.appendChild(ev);

        var conf = buildConfidence(data);
        if (conf) box.appendChild(conf);

        return box;
    }

    // ============================== 消息与请求 ==============================

    function append(role, node) {
        var wrap = el('<div class="msg ' + role + '"></div>');
        var bubble = el('<div class="bubble"></div>');
        if (typeof node === 'string') bubble.innerHTML = node;
        else bubble.appendChild(node);
        wrap.appendChild(bubble);
        S.chat.appendChild(wrap);
        scrollDown();
        return wrap;
    }

    function scrollDown() {
        S.content.scrollTop = S.content.scrollHeight;
    }

    /** 生成中 / 空闲态切换。
     *  第三批 ⑬ 起，生成中**不再禁用**发送键 —— 它此时是「停止生成」，必须可点。
     *  防重复提交改由 send() 开头的 `if (busy) return` 承担（别再靠按钮禁用，
     *  一旦把它 disable 就再也点不到停止）。 */
    function setBusy(flag) {
        busy = flag;
        S.send.classList.toggle('is-stop', flag);
        S.send.setAttribute('aria-label', flag ? '停止生成' : '发送');
        S.send.title = flag ? '停止生成' : '发送';
        S.input.disabled = flag;
        var qs = S.quickList.querySelectorAll('button');
        for (var i = 0; i < qs.length; i++) qs[i].disabled = flag;
    }

    /** 中止当前请求。
     *  诚实边界：Flask 是同步的，abort 只让**前端**不再等待，
     *  后端那次推理仍在进程里跑完（没有中断机制）。所以 UI 文案里明说了这一点，
     *  不在界面上假装「停止 = 省下这次计算」。 */
    function stopGeneration() {
        if (currentAbort) currentAbort.abort();
    }

    function pendingNode() {
        var node = el('<div></div>');
        node.appendChild(el('<div class="typing"><span></span><span></span><span></span></div>'));
        var hint = el('<p class="typing-hint">正在推理 · 已耗时 <b>0.0</b>s · 迭代上限 6 轮</p>');
        node.appendChild(hint);
        var t0 = Date.now();
        var timer = setInterval(function () {
            var b = hint.querySelector('b');
            if (b) b.textContent = ((Date.now() - t0) / 1000).toFixed(1);
        }, 100);
        node._stop = function () { clearInterval(timer); };
        return node;
    }

    async function send(question) {
        if (busy) return;
        var q = String(question === undefined ? S.input.value : question).trim();
        if (!q) return;

        // 窄屏：从抽屉里点了快捷问题，抽屉必须自己收起 —— 否则遮罩盖住答案，
        // 用户以为「点了没反应」。放在 send() 里而不是快捷问题自己的回调里，
        // 是为了让欢迎屏按钮、Enter 发送等所有入口都覆盖到。
        closeDrawer();

        if (firstQuestion) {
            S.welcome.hidden = true;
            firstQuestion = false;
        }

        append('user', '<span>' + esc(q) + '</span>');
        S.input.value = '';
        autoGrow();

        var pending = pendingNode();
        var holder = append('bot', '<span></span>');
        holder.querySelector('.bubble').innerHTML = '';
        holder.querySelector('.bubble').appendChild(pending);
        setBusy(true);

        // 每次请求一个独立 controller。上一请求结束后 currentAbort 已置 null，
        // 这里覆盖不会误中止别的请求。
        var ctrl = new AbortController();
        currentAbort = ctrl;

        try {
            var res = await fetch('/api/query', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ question: q }),
                signal: ctrl.signal
            });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            var data = await res.json();

            if (pending._stop) pending._stop();
            holder.querySelector('.bubble').innerHTML = '';
            holder.querySelector('.bubble').appendChild(buildAnswer(data));

            turns++;
            lastContext = data;
            updateHeader(q, data);
            if (data.run_status === 'error') checkHealth();
        } catch (err) {
            if (pending._stop) pending._stop();
            if (err && err.name === 'AbortError') {
                // 用户主动停止：不是错误态，backend 也没事，所以**不调 checkHealth**
                // （调了会把「后端在线」的状态条闪成检测中，误导成故障）。
                holder.querySelector('.bubble').innerHTML =
                    '<div class="run-stopped">已停止生成。'
                    + '<span class="run-stopped-note">后端推理可能仍在后台跑完，'
                    + '本次未等待结果。</span></div>';
            } else {
                holder.querySelector('.bubble').innerHTML =
                    '<div class="run-error">请求失败：' + esc(err.message || err)
                    + '。请确认后端已启动，且 MySQL 与 Ollama 在线。</div>';
                checkHealth();
            }
        } finally {
            currentAbort = null;
            setBusy(false);
            scrollDown();
            S.input.focus();
        }
    }

    function updateHeader(question, data) {
        S.convTitle.textContent = question;
        var bits = ['多轮会话 · 第 ' + Math.max(turns, 1) + ' 轮'];
        if (data.company) bits.push('企业=' + data.company);
        if (data.indicators && data.indicators.length) {
            bits.push('指标=' + data.indicators.join('/'));
        } else if (data.result_period) {
            bits.push('期间=' + data.result_period);
        }
        S.convMeta.textContent = bits.join(' · ');
    }

    function autoGrow() {
        S.input.style.height = 'auto';
        S.input.style.height = Math.min(S.input.scrollHeight, 140) + 'px';
    }

    // ============================== 健康检查 ==============================

    async function checkHealth() {
        S.statusDot.className = 'status-dot';
        S.statusText.textContent = '正在检测依赖…';
        try {
            var r = await fetch('/api/health', { credentials: 'same-origin' });
            var h = await r.json();
            S.statusText.textContent = (h.mysql ? 'MySQL 已连接' : 'MySQL 未连接')
                + ' · ' + (h.rag ? 'RAG 在线' : 'RAG 离线');
            S.statusDot.className = 'status-dot '
                + (h.mysql && h.rag ? 'ok' : (h.mysql ? 'warn' : 'bad'));
            S.statusPill.title = 'MySQL：' + (h.mysql_detail || '-')
                + '\nOllama：' + (h.rag_detail || '-')
                + '\n（点击重新检测）';
        } catch (e) {
            S.statusText.textContent = '后端未响应';
            S.statusDot.className = 'status-dot bad';
            S.statusPill.title = '无法访问 /api/health，请确认 app.py 已启动';
        }
    }

    // ============================== 初始化 ==============================

    (function initQuick() {
        for (var i = 0; i < QUICK.length; i++) {
            (function (item) {
                var b = el('<button type="button" class="qq">'
                    + '<span class="qq-tag">' + esc(item.tag) + '</span>'
                    + '<span class="qq-text">' + esc(item.t) + '</span>'
                    + '</button>');
                b.title = item.q;
                b.addEventListener('click', function () { send(item.q); });
                S.quickList.appendChild(b);
            })(QUICK[i]);
        }
        for (var j = 0; j < WELCOME.length; j++) {
            (function (item) {
                var b = el('<button type="button">' + esc(item.t) + '</button>');
                b.addEventListener('click', function () { send(item.q); });
                S.welcomeBtns.appendChild(b);
            })(WELCOME[j]);
        }
    })();

    S.input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            send();
        }
    });
    S.input.addEventListener('input', autoGrow);
    // 生成中同一个键改作「停止」。因为不再 disable，必须在这里分流 ——
    // 否则第二次点击会被 send() 开头的 busy 判断静默吞掉，按钮看起来像坏了。
    S.send.addEventListener('click', function () {
        if (busy) { stopGeneration(); return; }
        send();
    });
    S.statusPill.addEventListener('click', checkHealth);

    // 会话内事件委托：页签切换 / 完备度分解 / 图表放大
    S.chat.addEventListener('click', function (e) {
        var tab = e.target.closest('.ev-tab');
        if (tab) {
            var body = tab.closest('.ev-body');
            var name = tab.getAttribute('data-tab');
            var btns = body.querySelectorAll('.ev-tab');
            for (var i = 0; i < btns.length; i++) btns[i].classList.remove('is-on');
            tab.classList.add('is-on');
            var panels = body.querySelectorAll('.ev-panel');
            for (var j = 0; j < panels.length; j++) {
                panels[j].hidden = panels[j].getAttribute('data-panel') !== name;
            }
            return;
        }

        var toggle = e.target.closest('.conf-toggle');
        if (toggle) {
            var panel = toggle.closest('.conf-row').nextElementSibling;
            if (panel && panel.classList.contains('conf-panel')) {
                panel.hidden = !panel.hidden;
                toggle.textContent = panel.hidden ? '看算法' : '收起';
            }
            return;
        }

        var img = e.target.closest('.chart-wrap img');
        if (img) {
            S.lightboxImg.src = img.src;
            S.lightbox.classList.add('on');
        }
    });

    S.lightbox.addEventListener('click', function () {
        S.lightbox.classList.remove('on');
        S.lightboxImg.removeAttribute('src');
    });

    // ---------- 抽屉（≤900px 侧栏，第三批 ⑭）----------
    // 宽屏下按钮不显示（CSS 控制），这套逻辑空转无害；
    // 不做断点判断 —— 用 CSS 决定可见性、用 JS 只管状态，两边更不容易漂移。
    function openDrawer() {
        S.sidebar.classList.add('is-open');
        S.drawerMask.classList.add('is-open');
        S.drawerBtn.setAttribute('aria-expanded', 'true');
    }
    function closeDrawer() {
        S.sidebar.classList.remove('is-open');
        S.drawerMask.classList.remove('is-open');
        S.drawerBtn.setAttribute('aria-expanded', 'false');
    }
    S.drawerBtn.addEventListener('click', function () {
        if (S.sidebar.classList.contains('is-open')) closeDrawer();
        else openDrawer();
    });
    S.drawerMask.addEventListener('click', closeDrawer);

    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape') return;
        S.lightbox.classList.remove('on');
        closeDrawer();   // 抽屉也吃 Esc —— 否则窄屏打开后没法用键盘关
    });

    // 清空会话：必须同时清后端 session 槽位。
    // v2 只清 DOM，后端仍保留上一轮 company/indicator/period，
    // 用户以为清空了，下一句「那白云山呢」却继承了旧上下文。
    S.clearBtn.addEventListener('click', async function () {
        S.chat.innerHTML = '';
        S.welcome.hidden = false;
        firstQuestion = true;
        turns = 0;
        lastContext = null;
        S.convTitle.textContent = '新建会话';
        S.convMeta.textContent = '尚未提问 · 支持多轮追问，30 分钟内保持上下文';
        try {
            await fetch('/api/reset', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: '{}'
            });
        } catch (err) { /* 清空失败不阻塞界面，下次提问会新建会话 */ }
        S.input.focus();
    });

    checkHealth();
    S.input.focus();
})();
