# -*- coding: utf-8 -*-
"""table_extractor.py: 分块提取 + 评分回退
v3: 先扫描文档结构定位section，再在对应section内提取表格
"""
import pdfplumber, re
from collections import OrderedDict

TABLE_PROFILES = {
    "income_sheet": {
        "cn": "利润表", "section": ["利润表","二、"],
        "must_kw": ["营业收入","营业总收入","净利润"],
        "weight_kw": OrderedDict([
            ("营业总收入",30),("营业收入",28),("营业成本",15),
            ("营业利润",10),("利润总额",15),("净利润",25),
            ("归属于上市公司股东的净利润",25),
            ("销售费用",5),("管理费用",5),("财务费用",5),("研发费用",5),
        ]),
        "threshold":60,"exit_threshold":85,
        "section_markers": ["合并利润表", "利润表"],
    },
    "balance_sheet": {
        "cn": "资产负债表", "section": ["资产负债表","一、"],
        "must_kw": ["资产总计","总资产"],
        "weight_kw": OrderedDict([
            ("货币资金",15),("应收账款",8),("存货",8),
            ("流动资产",10),("固定资产",8),
            ("资产总计",25),
            ("负债合计",25),("总负债",20),
            ("所有者权益合计",25),("净资产",20),
            ("归属于上市公司股东的净资产",20),
            ("短期借款",8),("应付账款",8),("未分配利润",12),
        ]),
        "threshold":60,"exit_threshold":85,
        "section_markers": ["合并资产负债表", "资产负债表"],
    },
    "cash_flow_sheet": {
        "cn": "现金流量表", "section": ["现金流量表","三、"],
        "must_kw": ["经营活动"],
        "weight_kw": OrderedDict([
            ("经营活动产生的现金流量净额",28),("经营活动现金",22),
            ("投资活动产生的现金流量净额",18),("投资活动现金",15),
            ("筹资活动产生的现金流量净额",18),("筹资活动现金",15),
            ("现金及现金等价物",12),("现金净流量",15),
        ]),
        "threshold":50,"exit_threshold":80,
        "section_markers": ["合并现金流量表", "现金流量表"],
    },
    "core_performance_indicators_sheet": {
        "cn": "核心业绩指标",
        "section": ["主要会计数据","主要财务数据","主要财务指标"],
        "must_kw": ["每股收益","基本每股收益"],
        "weight_kw": OrderedDict([
            ("基本每股收益",25),("稀释每股收益",18),("每股收益",20),
            ("净资产收益率",18),("加权平均净资产收益率",22),
            ("归属于上市公司股东的净资产",18),("总资产",12),
            ("经营活动产生的现金流量净额",12),("每股净资产",12),
            ("营业收入",10),("营业总收入",10),
            ("净利润",15),("归属于上市公司股东的净利润",15),
            ("扣非净利润",15),
        ]),
        "threshold":55,"exit_threshold":82,
        "section_markers": ["主要会计数据", "主要财务数据", "主要财务指标"],
    },
}

SUMMARY_KW = ["营业收入","净利润","每股收益","总资产","净资产","加权平均净资产收益率"]

# 每张表的section结束标记（遇到这些说明下一张表开始了）
SECTION_END_MARKERS = {
    "income_sheet": ["资产负债表", "现金流量表", "主要会计数据", "主要财务数据"],
    "balance_sheet": ["利润表", "现金流量表", "主要会计数据", "主要财务数据"],
    "cash_flow_sheet": ["利润表", "资产负债表", "主要会计数据", "主要财务数据"],
    "core_performance_indicators_sheet": ["利润表", "资产负债表", "现金流量表"],
}


def extract_pages_for_type(pdf, table_type, section_pages, max_pages=50):
    """从section索引中提取指定表类型的页面数据
    返回: 最佳matrix，或None
    """
    pages = section_pages.get(table_type, [])
    if not pages:
        return None

    profile = TABLE_PROFILES[table_type]
    end_markers = SECTION_END_MARKERS.get(table_type, [])
    best_matrix = None
    best_score = -1

    for start_page in pages:
        # 检查这页是否真的有该表的数据（不只是标题提及）
        page_text = pdf.pages[start_page].extract_text() or ""
        content_check = score_page_for_type(page_text, table_type)
        if content_check[0] < 40:
            # 这页只是提到了表名，不是真正的表所在页，跳过
            continue

        # 如果起始页就有其他section的标记，说明多个section在同一页
        has_end_marker_on_start = any(m in page_text for m in end_markers)
        section_markers = TABLE_PROFILES[table_type].get("section_markers", [])
        has_own_marker = any(m in page_text for m in section_markers)

        if has_end_marker_on_start and not has_own_marker:
            # 本section的标记不在这里，跳过
            continue

        # 从start_page开始，连续扫描直到遇到下一个section的边界
        scan_range = [start_page]
        if has_end_marker_on_start:
            # 起始页已有其他section标记，只用这一页（不向前扩展）
            pass
        else:
            for pi in range(start_page + 1, min(start_page + 5, max_pages)):
                try:
                    text = pdf.pages[pi].extract_text() or ""
                except IndexError:
                    break
                # 如果遇到其他表的section标记，停止
                if any(m in text for m in end_markers):
                    break
                scan_range.append(pi)

        # 在扫描范围内提取表格（跨页合并）
        page_matrices = []
        for pi in scan_range:
            matrix = extract_matrix_from_page(pdf, pi, table_type=table_type)
            if not matrix or len(matrix.get('data_rows', [])) < 2:
                matrix = extract_borderless_table(pdf, pi)
            if matrix and len(matrix.get('data_rows', [])) >= 2:
                score = score_matrix_content(matrix, profile)
                page_matrices.append((pi, matrix, score))

        if not page_matrices:
            continue

        # 如果只有一页，直接取最好的
        if len(page_matrices) == 1:
            pi, matrix, score = page_matrices[0]
            if score > best_score:
                best_score = score
                best_matrix = preprocess_table(matrix)
                best_matrix['_page'] = pi
        else:
            # 跨页合并：取header_row来自最高分的页，data_rows合并所有页
            page_matrices.sort(key=lambda x: -x[2])
            best_pi, best_m, best_s = page_matrices[0]
            merged_header = best_m.get('header_row', [])
            merged_rows = []
            seen_labels = set()
            for pi, m, s in page_matrices:
                for row in m.get('data_rows', []):
                    label = str(row[0] or "").strip()
                    if label and label not in seen_labels:
                        merged_rows.append(row)
                        seen_labels.add(label)
                    elif not label:
                        merged_rows.append(row)
            if merged_rows:
                merged_matrix = {'header_row': merged_header, 'data_rows': merged_rows}
                merged_score = score_matrix_content(merged_matrix, profile)
                if merged_score > best_score:
                    best_score = merged_score
                    best_matrix = preprocess_table(merged_matrix)
                    best_matrix['_page'] = best_pi

    return best_matrix


def score_matrix_content(matrix, profile):
    """对已提取的matrix评分（基于内容匹配，不看页面位置）"""
    if not matrix or not matrix.get('data_rows'):
        return 0
    all_text = ""
    for row in [matrix.get('header_row', [])] + matrix['data_rows']:
        all_text += " ".join(str(c or "") for c in row) + " "

    score = 0
    for kw, pts in profile.get('weight_kw', {}).items():
        if kw in all_text:
            score += pts
    return score


def score_page_for_type(page_text, table_type, title_context=None):
    """对单页文本，按目标表类型评分子维度得分（无800字符限制）"""
    profile = TABLE_PROFILES[table_type]
    text_all = page_text
    scores = {}

    # 标题匹配（全文扫描，不再限制800字符）
    hs = 0
    for kw in profile["section"]:
        if kw in text_all: hs += 15
    scores["header"] = min(hs, 30)

    # M2: 标题继承得分
    ts = 0
    if title_context:
        for poff, tline in title_context:
            wt = max(15 - poff * 5, 5)
            if any(k in tline for k in profile["section"]):
                ts += wt
            elif any(k in tline for k in ["主要","财务数据","财务指标","会计数据"]):
                ts += wt // 2
    scores["title_inheritance"] = min(ts, 15)

    # 表格结构（最重要 - 必须先算，用于调节其他分数）
    tbl_lines = [l for l in text_all.split("\n") if len(l.strip())>10 and re.search(r"\d",l)]
    tbl_count = len(tbl_lines)
    scores["table_structure"] = min(tbl_count, 20)

    # 关键词密度 - 如果没有足够表格结构，大幅降低关键词得分
    # 叙述文字中也可能出现关键词，需要表格行支撑
    kw_score = 0
    for kw, pts in profile["weight_kw"].items():
        cnt = text_all.count(kw)
        if cnt > 0: kw_score += pts * min(cnt, 3)
    raw_kw = min(max(kw_score // 5, 0), 25)
    if tbl_count < 10:
        # 表格行不够，关键词可能只是叙述文字中的提及
        raw_kw = raw_kw * tbl_count // 10
    scores["subject_density"] = raw_kw

    # 数值密度 - 同样需要表格结构支撑
    nums = re.findall(r"[+-]?(?:\d[\d,]*\.?\d*)(?:万|亿|元)?", text_all)
    big = [n for n in nums if len(n.replace(",","").replace(".",""))>=5 or "亿" in n or "万" in n]
    raw_num = min(len(big)*3, 25)
    if tbl_count < 10:
        raw_num = raw_num * tbl_count // 10
    scores["number_density"] = raw_num

    total = sum(scores.values())
    return total, scores


def detect_fuzhu_column(header_row):
    """检测表头是否含附注列，返回目标数值列索引"""
    if not header_row: return 1
    for i,h in enumerate(header_row):
        if chr(38468) in h:
            for j in range(i+1,len(header_row)):
                if header_row[j].strip(): return j
            return i+1
    return 1


def extract_matrix_from_page(pdf, page_idx, table_type=None):
    """从指定页提取表格矩阵"""
    try: page = pdf.pages[page_idx]
    except IndexError: return None
    tables = page.extract_tables()
    if not tables: return None

    type_kw = set()
    if table_type:
        profile = TABLE_PROFILES.get(table_type, {})
        type_kw = set(profile.get("weight_kw", {}).keys()) if profile else set()
    header_kw = ["项目","附注","期末余额","期初余额","本期金额","上期金额","本报告期","上年同期","报告期","金额"]

    scored_tables = []
    for tbl in tables:
        if not tbl or len(tbl) < 2: continue
        content_score = 0
        if type_kw:
            tbl_text = " ".join(" ".join(str(c or "") for c in r) for r in tbl)
            matches = sum(1 for kw in type_kw if kw in tbl_text)
            content_score = min(matches * 8, 60)
        header_score = 0
        for i in range(min(3, len(tbl))):
            rt = "".join(str(c or "") for c in tbl[i])
            if any(kw in rt for kw in header_kw):
                header_score = 25
                break
        total = content_score + header_score
        scored_tables.append((total, content_score, len(tbl), tbl))

    scored_tables.sort(key=lambda x: (x[1], x[2]), reverse=True)
    if not scored_tables: return None
    best = scored_tables[0][3]
    if not best or len(best) < 2: return None

    header_row, header_idx = None, 0
    for i in range(min(3, len(best))):
        rt = "".join(str(c or "") for c in best[i])
        if any(kw in rt for kw in header_kw):
            header_row = [str(c or "").strip() for c in best[i]]
            header_idx = i
            break
    if not header_row:
        header_row = [str(c or "").strip() for c in best[0]]

    data_rows = []
    for row in best[header_idx+1:]:
        cl = [str(c or "").strip() for c in row]
        if any(c for c in cl if len(c) > 0):
            data_rows.append(cl)
    return {"header_row": header_row, "data_rows": data_rows}


def get_title_context(pdf, page_idx, n_above=3):
    """向上取最近的标题行作为上下文 (M2)"""
    titles = []
    for offset in range(1, min(n_above, page_idx) + 1):
        try:
            text = pdf.pages[page_idx - offset].extract_text() or ''
            for line in text.split(chr(10)):
                line = line.strip()
                if not line: continue
                if any(kw in line for kw in [chr(12289), chr(20027), chr(35201), chr(21033), chr(36164), chr(29616), chr(36130), chr(25454), chr(25351), chr(35745), chr(20250)]):
                    titles.append((offset, line))
                    break
        except IndexError: break
    return titles


def classify_page_type(page_idx, page_text):
    """判断页类型: summary(摘要) | statement(完整报表)"""
    if page_idx == 0:
        sc = sum(1 for kw in SUMMARY_KW if kw in page_text)
        has_section = any(s in page_text[:600] for s in ["主要会计数据","利润表","资产负债表"])
        if sc >= 3 and not has_section: return "summary"
    return "statement"


def _scan_global_unit(pdf, max_pages=50):
    """扫描整个PDF，找到所有单位声明，返回最常见的单位倍率
    改进：统计所有声明（包括元），不只是非1的值
    """
    from pdf_extractor.value_normalizer import detect_unit
    unit_counts = {}
    n = min(len(pdf.pages), max_pages)
    for i in range(n):
        text = pdf.pages[i].extract_text() or ""
        uf, found = detect_unit(text)
        if found:  # 找到了明确的单位声明就统计，不管是不是元
            unit_counts[uf] = unit_counts.get(uf, 0) + 1
    if not unit_counts:
        return 1
    # 返回出现最多的单位倍率
    return max(unit_counts, key=unit_counts.get)


def extract_all_tables(pdf_path, max_pages=50):
    """主提取函数: 分块优先，评分回退"""
    try:
        pdf = pdfplumber.open(pdf_path)
    except Exception:
        return {}

    n_pages = min(len(pdf.pages), max_pages)

    # ===== 第零步：全局单位扫描 =====
    global_unit_factor = _scan_global_unit(pdf, max_pages)

    # ===== 第一步：M1语义分块定位 =====
    from pdf_extractor.pdf_segmenter import get_section_pages
    section_pages, blocks = get_section_pages(pdf_path, max_pages)

    # ===== 第二步：分块提取 =====
    result = {}
    n_pages_total = min(len(pdf.pages), max_pages)
    from pdf_extractor.value_normalizer import detect_unit_above
    for tt in TABLE_PROFILES:
        matrix_data = extract_pages_for_type(pdf, tt, section_pages, max_pages)
        if matrix_data:
            page_idx = matrix_data.get('_page', section_pages.get(tt, [0])[0])
            # 检测单位（上下都找，扩展搜索范围）
            above_texts = []
            for pi in range(page_idx-1, max(page_idx-6,-1), -1):
                if pi < 0: break
                pt = pdf.pages[pi].extract_text() or ""
                if pt.strip(): above_texts.append((pt, pi))
            below_texts = []
            for pi in range(page_idx+1, min(page_idx+4, n_pages_total)):
                pt = pdf.pages[pi].extract_text() or ""
                if pt.strip(): below_texts.append((pt, pi))
            page_text = pdf.pages[page_idx].extract_text() or ""
            unit_factor, unit_raw, unit_source_text = detect_unit_above(page_text, above_texts, below_texts, global_unit_factor)

            result[tt] = {
                "score": 100,  # 分块提取的置信度设为100
                "page": page_idx,
                "page_text": page_text,
                "matrix": matrix_data,
                "unit_factor": unit_factor,
                "unit_raw": unit_raw,
                "unit_source_text": unit_source_text,
                "scores_breakdown": {"method": "section_based"},
                "source_type": "statement",
            }

    # ===== 第三步：评分回退（分块没找到的表） =====
    missing = [tt for tt in TABLE_PROFILES if tt not in result]
    if missing:
        best = {}
        for tt in missing:
            best[tt] = {"score":0,"page":-1,"page_text":"","matrix":None,"unit_factor":1,"unit_raw":"元","unit_source_text":"","scores_breakdown":{},"source_type":"statement"}

        for page_idx in range(n_pages):
            page = pdf.pages[page_idx]
            page_text = page.extract_text() or ""
            if not page_text.strip(): continue

            source_type = classify_page_type(page_idx, page_text)
            still_searching = False

            for tt in missing:
                bp = best[tt]
                if bp["score"] >= TABLE_PROFILES[tt]["exit_threshold"]: continue
                still_searching = True

                titles = get_title_context(pdf, page_idx) if page_idx > 0 else []
                total, breakdown = score_page_for_type(page_text, tt, title_context=titles)
                if total > bp["score"]:
                    bp["score"] = total; bp["page"] = page_idx
                    bp["page_text"] = page_text; bp["scores_breakdown"] = breakdown
                    bp["source_type"] = source_type

                    matrix = extract_matrix_from_page(pdf, page_idx, table_type=tt)
                    if not matrix or len(matrix.get('data_rows',[])) < 2:
                        matrix = extract_borderless_table(pdf, page_idx)
                    if matrix and len(matrix.get('data_rows',[])) >= 2:
                        bp["matrix"] = preprocess_table(matrix)
                        above_texts = []
                        for pi in range(page_idx-1, max(page_idx-6,-1), -1):
                            if pi < 0: break
                            pt = pdf.pages[pi].extract_text() or ""
                            if pt.strip(): above_texts.append((pt, pi))
                        below_texts = []
                        for pi in range(page_idx+1, min(page_idx+4, n_pages)):
                            pt = pdf.pages[pi].extract_text() or ""
                            if pt.strip(): below_texts.append((pt, pi))
                        unit_factor, unit_raw, unit_source_text = detect_unit_above(page_text, above_texts, below_texts, global_unit_factor)
                        bp["unit_factor"] = unit_factor
                        bp["unit_raw"] = unit_raw
                        bp["unit_source_text"] = unit_source_text

            if not still_searching: break

        # 合并回退结果
        for tt in missing:
            bp = best[tt]
            if bp["page"]>=0 and bp["score"]>=TABLE_PROFILES[tt]["threshold"]:
                result[tt] = bp
            elif bp["page"]>=0:
                para_data = extract_from_paragraphs(pdf, bp["page"], table_type=tt)
                if para_data:
                    bp["para_data"] = para_data
                    bp["source_type"] = "paragraph"
                    result[tt] = bp

    pdf.close()
    return result


def extract(pdf_path, max_pages=50):
    """旧接口兼容: 取最高分表"""
    all_t = extract_all_tables(pdf_path, max_pages)
    if not all_t: return None
    best_tt = max(all_t, key=lambda k: all_t[k]["score"])
    return all_t[best_tt]


def extract_borderless_table(pdf, page_idx):
    """无框线表格: 用文字坐标重建二维矩阵"""
    try:
        page = pdf.pages[page_idx]
    except IndexError:
        return None
    words = page.extract_words(keep_blank_chars=True, x_tolerance=3)
    if not words or len(words) < 6:
        return None
    rows = {}
    for w in words:
        y_key = round(w["top"] / 3) * 3
        rows.setdefault(y_key, []).append(w)
    sorted_y = sorted(rows.keys())
    if len(sorted_y) < 3:
        return None
    all_x = []
    for w in words:
        all_x.append(w["x0"]); all_x.append(w["x1"])
    all_x.sort()
    x_clusters = [[all_x[0]]]
    for x in all_x[1:]:
        if x - x_clusters[-1][-1] > 15:
            x_clusters.append([x])
        else:
            x_clusters[-1].append(x)
    col_bounds = [(min(c), max(c)) for c in x_clusters if len(c) >= 2]
    if len(col_bounds) < 2:
        return None
    matrix = []
    for y_key in sorted_y:
        row_words = rows[y_key]
        row = [""] * len(col_bounds)
        for w in row_words:
            cx = (w["x0"] + w["x1"]) / 2
            for ci, (x_min, x_max) in enumerate(col_bounds):
                if x_min <= cx <= x_max:
                    row[ci] = (row[ci] + " " + w["text"]).strip()
                    break
        if any(cell for cell in row if cell.strip()):
            matrix.append(row)
    if len(matrix) < 3:
        return None
    header_row = None
    header_idx = 0
    for i in range(min(3, len(matrix))):
        rt = "".join(matrix[i])
        if any(kw in rt for kw in [chr(39033), chr(38468), chr(26399), chr(26412)]):
            header_row = matrix[i]; header_idx = i; break
    if not header_row:
        header_row = matrix[0]
    data_rows = matrix[header_idx+1:]
    return {"header_row": header_row, "data_rows": data_rows, "source": "borderless"}


def filter_sparse_columns(rows, threshold=0.30):
    if not rows:
        return rows
    n_cols = max(len(r) for r in rows)
    keep_cols = []
    for ci in range(n_cols):
        non_empty = 0
        for row in rows:
            if ci < len(row) and row[ci] and str(row[ci]).strip():
                non_empty += 1
        ratio = non_empty / len(rows)
        if ratio >= threshold:
            keep_cols.append(ci)
    if len(keep_cols) == n_cols:
        return rows
    result = []
    for row in rows:
        new_row = []
        for ci in keep_cols:
            new_row.append(row[ci] if ci < len(row) else "")
        result.append(new_row)
    if len(keep_cols) <= 1:
        return rows
    return result


def merge_multirow_header(rows, header_rows=2):
    if len(rows) <= header_rows:
        return (rows[0] if rows else [], rows[1:] if len(rows) > 1 else [])
    headers = rows[:header_rows]
    data = rows[header_rows:]
    n_cols = max(len(r) for r in headers)
    merged = []
    for ci in range(n_cols):
        parts = []
        for row in headers:
            cell = str(row[ci]).strip() if ci < len(row) else ""
            if cell:
                parts.append(cell)
        merged.append("".join(parts) if parts else "")
    return merged, data


def merge_split_rows(data_rows):
    result = []
    i = 0
    while i < len(data_rows):
        row = data_rows[i]
        label = str(row[0]).strip() if row else ""
        values = row[1:] if len(row) > 1 else []
        all_empty = True
        has_label = bool(label)
        for v in values:
            vs = str(v).strip()
            if vs and vs not in ("", "-", "--", chr(8212)):
                all_empty = False
                break
        if has_label and all_empty and i + 1 < len(data_rows):
            next_row = data_rows[i + 1]
            next_label = str(next_row[0]).strip() if next_row else ""
            next_values = next_row[1:] if len(next_row) > 1 else []
            merged_label = label + next_label
            result.append([merged_label] + list(next_values))
            i += 2
        else:
            result.append(row)
            i += 1
    return result


def preprocess_table(matrix):
    if not matrix or not matrix.get("data_rows"):
        return matrix
    header_row = matrix.get("header_row", [])
    data_rows = matrix.get("data_rows", [])
    all_rows = [header_row] + data_rows
    all_rows = filter_sparse_columns(all_rows)
    if len(all_rows) >= 3:
        merged_header, new_data_rows = merge_multirow_header(all_rows, header_rows=2)
    elif len(all_rows) >= 2:
        merged_header, new_data_rows = all_rows[0], all_rows[1:]
    else:
        merged_header, new_data_rows = all_rows[0] if all_rows else [], []
    new_data_rows = merge_split_rows(new_data_rows)
    matrix["header_row"] = merged_header
    matrix["data_rows"] = new_data_rows
    return matrix


def extract_from_paragraphs(pdf, page_idx, table_type=None):
    """段落式数据提取: 从文本中找关键词+数字"""
    try:
        text = pdf.pages[page_idx].extract_text() or ""
    except IndexError:
        return None
    if not text.strip():
        return None
    import re as _re
    from pdf_extractor.value_normalizer import parse_number as _pn
    found = {}
    key_patterns = [
        ("total_operating_revenue", "income_sheet", r"(?:营业总收入|营业收入|营收)[：:]\s*([+-]?[\d,]+(?:\\.\d+)?)"),
        ("net_profit", "income_sheet", r"(?:归母净利润|归属于上市公司股东的净利润|净利润)[：:]\s*([+-]?[\d,]+(?:\\.\d+)?)"),
        ("eps", "core_performance_indicators_sheet", r"基本每股收益[：:]\s*([+-]?[\d,]+(?:\\.\d+)?)"),
        ("roe_weighted_excl_non_recurring", "core_performance_indicators_sheet", r"加权平均净资产收益率[：:]\s*([+-]?[\d,]+(?:\\.\d+)?)%"),
        ("asset_total_assets", "balance_sheet", r"(?:资产总计|总资产)[：:]\s*([+-]?[\d,]+(?:\\.\d+)?)"),
        ("total_operating_revenue", "core_performance_indicators_sheet", r"营业总收入[：:]\s*([+-]?[\d,]+(?:\\.\d+)?)"),
    ]
    for fname, tbl, pat in key_patterns:
        m = _re.search(pat, text)
        if m:
            v = _pn(m.group(1))
            if v is not None:
                tbl_data = found.setdefault(tbl, {})
                tbl_data[fname] = v
    return found if found else None
