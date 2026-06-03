# -*- coding: utf-8 -*-
"""value_normalizer.py: M5 数值标准化 + 量纲统一
改造:
  1. 增强单位检测: 扫描表格上下方的单位声明，识别"单位：万元""单位：千元"等
  2. 跨章节判断: 单位声明和当前表在同一章节内才继承
  3. 混用单位处理: EPS用元，其他用万元时特殊处理
"""
import re

# 单位匹配模式（权重从高到低）
UNIT_PATTERNS = [
    (r'单位[：:　]\s*人民币元\s*\(本表\)?', 1),
    (r'单位[：:　]\s*元(?![\w一-鿿])', 1),
    (r'单位[：:　]\s*千元', 1000),
    (r'人民币千元', 1000),
    (r'单位[：:　]\s*万元', 10000),
    (r'人民币万元', 10000),
    (r'单位[：:　]\s*亿元', 100000000),
    (r'人民币亿元', 100000000),
    (r'金额单位[：:　]元', 1),
    (r'金额单位[：:　]千元', 1000),
    (r'金额单位[：:　]万元', 10000),
    (r'金额单位[：:　]亿元', 100000000),
    (r'单位[：:　]人民币元', 1),
    # 常见顺序模式: "以下数据单位均为人民币万元"
    (r'单位均为人民币(?:万|千|亿)?元', None),  # 需进一步解析
    (r'以下数据.*?单位[：:].*?(?:万|千|亿)?元', None),  # 需进一步解析
]

# 进一步解析"以下数据单位为万元"格式
UNIT_IN_TEXT = [
    (r'单位[：:](?:人民币)?(千)元', 1000),
    (r'单位[：:](?:人民币)?(万)元', 10000),
    (r'单位[：:](?:人民币)?(亿)元', 100000000),
    (r'单位均为人民币(千)元', 1000),
    (r'单位均为人民币(万)元', 10000),
    (r'单位均为人民币(亿)元', 100000000),
]

# 章节边界标记
SECTION_BOUNDARIES = [
    "一、", "二、", "三、", "四、", "五、", "六、", "七、", "八、", "九、", "十、",
    "（一）", "（二）", "（三）", "（四）", "（五）",
    "利润表", "资产负债表", "现金流量表",
    "主要会计数据", "主要财务数据", "主要财务指标", "财务报表",
]

# 每股类字段（单位是元/股，不参与表格级倍率）
UNIT_FIELDS = {'eps', 'net_asset_per_share', 'operating_cf_per_share'}

# 比率类字段（存小数，不是原始数值）
RATIO_FIELDS = {'roe', 'roe_weighted_excl_non_recurring', 'gross_profit_margin',
    'net_profit_margin', 'operating_cf_ratio_of_net_cf', 'investing_cf_ratio_of_net_cf',
    'financing_cf_ratio_of_net_cf', 'asset_liability_ratio',
    'operating_revenue_yoy_growth', 'operating_revenue_qoq_growth',
    'net_profit_yoy_growth', 'net_profit_qoq_growth',
    'net_profit_excl_non_recurring_yoy',
    'asset_total_assets_yoy_growth', 'liability_total_liabilities_yoy_growth',
    'net_cash_flow_yoy_growth'}


def detect_unit(text):
    """从文本中检测单位声明，返回 (倍率因子, 是否找到声明)
    返回 (factor, True) 表示找到了明确的单位声明
    返回 (1, False) 表示没找到任何单位声明
    """
    if not text: return (1, False)

    for pat, factor in UNIT_PATTERNS:
        if re.search(pat, text):
            if factor is None:
                for pat2, factor2 in UNIT_IN_TEXT:
                    if re.search(pat2, text): return (factor2, True)
                return (1, True)  # 找到了声明但解析不出倍率
            return (factor, True)

    for pat2, factor2 in UNIT_IN_TEXT:
        if re.search(pat2, text): return (factor2, True)

    return (1, False)


def _has_section_boundary(text_a, text_b):
    """判断两段文本之间是否有章节边界"""
    for marker in SECTION_BOUNDARIES:
        if marker in text_a or marker in text_b:
            return True
    return False


def detect_unit_above(main_text, above_texts, below_texts=None, global_unit_factor=None):
    """扫描单位声明（优先本页，再上下页，最后全局兜底）

    返回: (unit_factor, unit_raw, unit_source_text)
    - unit_factor: 单位倍率
    - unit_raw: 原始单位名称（如"万元"、"元"）
    - unit_source_text: 单位声明原文
    """
    # 先查本页（在section边界之前的部分）
    boundary_pos = len(main_text)
    for marker in SECTION_BOUNDARIES:
        pos = main_text.find(marker)
        if pos != -1 and pos < boundary_pos:
            boundary_pos = pos
    text_before_boundary = main_text[:boundary_pos]
    uf, found, source_text = detect_unit_with_source(text_before_boundary)
    if found: return uf, _get_unit_name(uf), source_text

    # 也检查全文
    uf, found, source_text = detect_unit_with_source(main_text)
    if found: return uf, _get_unit_name(uf), source_text

    # 向上扫描（最多5页，遇到section边界停止）
    for page_text, page_idx in above_texts[:5]:
        has_boundary = any(m in page_text for m in SECTION_BOUNDARIES)
        if has_boundary:
            uf, found, source_text = detect_unit_with_source(page_text)
            if found:
                return uf, _get_unit_name(uf), source_text
            break
        uf, found, source_text = detect_unit_with_source(page_text)
        if found:
            return uf, _get_unit_name(uf), source_text

    # 向下扫描（最多3页）
    if below_texts:
        for page_text, page_idx in below_texts[:3]:
            has_boundary = any(m in page_text for m in SECTION_BOUNDARIES)
            if has_boundary:
                uf, found, source_text = detect_unit_with_source(page_text)
                if found:
                    return uf, _get_unit_name(uf), source_text
                break
            uf, found, source_text = detect_unit_with_source(page_text)
            if found:
                return uf, _get_unit_name(uf), source_text

    # 全局兜底（只在没有找到任何单位声明时使用）
    # 注意：如果找到了单位声明但解析失败，不应该使用全局兜底
    # 因为不同表格可能有不同的单位
    return 1, "元", "default"


def detect_unit_with_source(text):
    """从文本中检测单位声明，返回 (倍率因子, 是否找到声明, 声明原文)"""
    if not text: return (1, False, "")

    for pat, factor in UNIT_PATTERNS:
        match = re.search(pat, text)
        if match:
            if factor is None:
                for pat2, factor2 in UNIT_IN_TEXT:
                    match2 = re.search(pat2, text)
                    if match2: return (factor2, True, match2.group(0))
                return (1, True, match.group(0))
            return (factor, True, match.group(0))

    for pat2, factor2 in UNIT_IN_TEXT:
        match = re.search(pat2, text)
        if match: return (factor2, True, match.group(0))

    return (1, False, "")


def _get_unit_name(factor):
    """根据倍率因子返回单位名称"""
    if factor == 1:
        return "元"
    elif factor == 1000:
        return "千元"
    elif factor == 10000:
        return "万元"
    elif factor == 100000000:
        return "亿元"
    else:
        return "未知"


def parse_number(raw):
    """解析数值字符串为float，支持%, *, 脚注, 空格等"""
    if raw is None: return None
    t = str(raw).strip()
    if not t or t in ('-', '--', chr(8212), chr(19981)+chr(33046), 'N/A', '', '——'):
        return None
    # 括号表示负数
    if t[:1] == chr(40) and t[-1:] == chr(41):
        try: return -float(t[1:-1].replace(',', ''))
        except: return None
    # 去除: 逗号、全角逗号、空格、*号脚注、%号(后面比率字段会处理)
    t = t.replace(',', '').replace(chr(65292), '').replace(' ', '')
    t = t.rstrip('*').rstrip(chr(8224))  # 去除尾部脚注标记
    t = t.replace('%', '')  # 去除百分号，比率字段在normalize里处理
    if not t: return None
    try: return float(t)
    except: return None


def normalize(raw, unit_factor=1, field_name=None):
    """数值标准化为万元为单位

    规则:
      - 每股类(eps/nav): 保留元, 不参与倍率
      - 比率类(roe/毛利率等): 保留小数, 超过1的除以100
      - 其他: 统一转为万元 (v * unit_factor / 10000)
    """
    v = parse_number(raw)
    if v is None: return None

    # 每股类: 不参与表格倍率
    if field_name and field_name in UNIT_FIELDS:
        return round(v, 4)

    # 比率类: 存小数
    if field_name and field_name in RATIO_FIELDS:
        v = round(v, 4)
        if abs(v) > 1: v = v / 100
        return round(v, 6)

    # 普通数值: v * unit_factor / 10000 -> 转为万元
    v = round(v * unit_factor / 10000, 4)
    return None if abs(v) > 1e12 else v


def normalize_with_page(raw, page_text, field_name=None):
    """对单页提取的数值标准化"""
    uf, _ = detect_unit(page_text)
    return normalize(raw, uf, field_name)
