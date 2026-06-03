# -*- coding: utf-8 -*-
"""
时间归一化模块 (TimeNormalizer)

支持：
  裸年份    2023, 2024
  带年字    2023年, 2024年度
  短年份    23年, 24年
  季度      2023Q1, 2023一季度, 2023三季报
  半年报    2024H1, 2024半年报, 2024中报
  年报      2024年报, 2024年度报告
  相对时间  去年, 前年, 今年, 最新
  区间      2022到2024, 2022-2024
  趋势      近三年, 近五年
"""

import re
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional

# ============================================================
# 常量
# ============================================================

CURRENT_YEAR = datetime.now().year

# 中文数字
CN_NUM = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
           '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}

# 季度关键词 → quarter number
QUARTER_MAP = {
    '一季度': 1, '一季报': 1, 'Q1': 1, 'q1': 1,
    '二季度': 2, '二季报': 2, 'Q2': 2, 'q2': 2,
    '三季度': 3, '三季报': 3, '前三季度': 3, 'Q3': 3, 'q3': 3,
    '四季度': 4, '四季报': 4, '年报': 4, '年度': 4, 'FY': 4, 'fy': 4,
}

# 半年报关键词 → half number
HALF_MAP = {
    '半年报': 1, '半年度': 1, '上半年': 1, '中报': 1, 'H1': 1, 'h1': 1,
    '下半年': 2, 'H2': 2, 'h2': 2,
}

# 报告期关键词 → report_period code
PERIOD_KEYWORDS = {
    '年报': 'FY', '年度': 'FY', '全年': 'FY',
    '半年报': 'H1', '半年度': 'H1', '上半年': 'H1', '中报': 'H1',
    '一季报': 'Q1', '一季度': 'Q1', '第一季度': 'Q1',
    '三季报': 'Q3', '三季度': 'Q3', '前三季度': 'Q3', '第三季度': 'Q3',
}


# ============================================================
# 数据结构
# ============================================================

@dataclass
class TimeExpression:
    """统一的时间表达式"""
    type: str                                    # year / quarter / half / range / rolling / relative / latest
    year: Optional[int] = None                   # 精确年份
    quarter: Optional[int] = None                # 1-4
    half: Optional[int] = None                   # 1-2
    start_year: Optional[int] = None             # 区间起始
    end_year: Optional[int] = None               # 区间结束
    rolling_years: Optional[int] = None          # 近N年
    period: Optional[str] = None                 # FY / Q1 / H1 / Q3 (供SQL用)
    label: str = ''                              # 人类可读标签

    def to_dict(self):
        """兼容旧接口"""
        d = {'type': self.type, 'label': self.label, 'period': self.period}
        if self.year is not None:
            d['year'] = self.year
        if self.start_year is not None:
            d['year_start'] = self.start_year
            d['year_end'] = self.end_year
        if self.rolling_years is not None:
            d['year_start'] = CURRENT_YEAR - self.rolling_years
            d['year_end'] = CURRENT_YEAR - 1
        if self.type == 'latest':
            d['label'] = '最新数据'
        return d


# ============================================================
# 正则模式（按优先级排列）
# ============================================================

# 年份范围: "2022年到2024年", "2022-2024", "2022~2024"
_RE_RANGE = re.compile(r'(20\d{2})\s*[-~到至—]\s*(20\d{2})')

# 趋势: "近三年", "近五年", "最近3年"
_RE_ROLLING = re.compile(r'近(\d|[一二两三四五六七八九十]+)年')

# 季度: "2023Q1", "2023一季度", "2023三季报"
_RE_QUARTER = re.compile(
    r'(20\d{2})\s*(?:'
    r'[Qq]([1-4])'
    r'|([一二三四])季度'
    r'|([一二三四])季报'
    r'|(前三)季度'   # 前三季度 = Q3
    r'|(年报|年度|FY)'  # 年报 = Q4
    r')'
)

# 半年报: "2024H1", "2024半年报", "2024中报"
_RE_HALF = re.compile(
    r'(20\d{2})\s*(?:'
    r'[Hh]([12])'
    r'|(半)年报'
    r'|(半)年度'
    r'|(上半)年'
    r'|(中)报'
    r'|(下半)年'
    r')'
)

# 带"年"字的年份: "2023年", "23年"
_RE_YEAR_CN = re.compile(r'(20\d{2})年')
_RE_SHORT_YEAR = re.compile(r'(?<![A-Za-z\d])(\d{2})年')

# 裸年份: "2023" (前后不能有数字)
_RE_BARE_YEAR = re.compile(r'(?<!\d)(20\d{2})(?!\d)')

# 报告期关键词（独立出现，不带年份）
_RE_PERIOD_ONLY = re.compile(r'(年报|半年报|中报|一季报|三季报|季度报)')


# ============================================================
# 核心解析
# ============================================================

def _parse_time(text: str, current_year: int = CURRENT_YEAR) -> TimeExpression:
    """主解析函数，按优先级匹配"""

    if not text:
        return TimeExpression(type='latest', label='未指定时间')

    # ---- 1. 区间 ----
    m = _RE_RANGE.search(text)
    if m:
        y1, y2 = int(m.group(1)), int(m.group(2))
        return TimeExpression(
            type='range', start_year=min(y1, y2), end_year=max(y1, y2),
            label='%d-%d年' % (min(y1, y2), max(y1, y2))
        )

    # ---- 2. 趋势 (近三年) ----
    m = _RE_ROLLING.search(text)
    if m:
        raw = m.group(1)
        n = CN_NUM.get(raw, int(raw) if raw.isdigit() else 3)
        return TimeExpression(
            type='rolling', rolling_years=n,
            label='近%d年' % n
        )

    # ---- 3. 季度 (2023Q1, 2023一季度) ----
    m = _RE_QUARTER.search(text)
    if m:
        year = int(m.group(1))
        # 从各捕获组确定季度
        q = None
        period = None
        if m.group(2):  # Q1-Q4
            q = int(m.group(2))
        elif m.group(3):  # 一季度~四季度
            q = CN_NUM.get(m.group(3), None)
        elif m.group(4):  # 一季报~四季报
            q = CN_NUM.get(m.group(4), None)
        elif m.group(5):  # 前三季度
            q = 3
        elif m.group(6):  # 年报/年度/FY
            q = 4

        if q:
            period = {1: 'Q1', 2: 'Q2', 3: 'Q3', 4: 'FY'}.get(q, 'FY')
            label = '%d年%s' % (year, period)
            return TimeExpression(type='quarter', year=year, quarter=q, period=period, label=label)

    # ---- 4. 半年报 (2024H1, 2024半年报) ----
    m = _RE_HALF.search(text)
    if m:
        year = int(m.group(1))
        h = None
        if m.group(2):  # H1/H2
            h = int(m.group(2))
        elif m.group(3) or m.group(4) or m.group(5) or m.group(6):  # 半年报/半年度/上半年/中报
            h = 1
        elif m.group(7):  # 下半年
            h = 2

        if h:
            period = 'H1' if h == 1 else 'H2'
            label = '%d年%s' % (year, period)
            return TimeExpression(type='half', year=year, half=h, period=period, label=label)

    # ---- 5. 带"年"字的年份 (2023年, 23年) ----
    m = _RE_YEAR_CN.search(text)
    if m:
        year = int(m.group(1))
        # 检查后面是否紧跟季度/半年报关键词
        period = _extract_period_suffix(text, m.end())
        if period:
            return TimeExpression(type='year', year=year, period=period, label='%d年%s' % (year, period))
        return TimeExpression(type='year', year=year, label='%d年' % year)

    m = _RE_SHORT_YEAR.search(text)
    if m:
        year = 2000 + int(m.group(1))
        period = _extract_period_suffix(text, m.end())
        if period:
            return TimeExpression(type='year', year=year, period=period, label='%d年%s' % (year, period))
        return TimeExpression(type='year', year=year, label='%d年' % year)

    # ---- 6. 相对时间 ----
    if '去年' in text:
        return TimeExpression(type='relative', year=current_year - 1, label='去年')
    if '前年' in text:
        return TimeExpression(type='relative', year=current_year - 2, label='前年')
    if '今年' in text or '本年' in text:
        return TimeExpression(type='relative', year=current_year, label='今年')

    # ---- 7. 裸年份 (2023, 2024) ----
    m = _RE_BARE_YEAR.search(text)
    if m:
        year = int(m.group(1))
        period = _extract_period_suffix(text, m.end())
        if period:
            return TimeExpression(type='year', year=year, period=period, label='%d年%s' % (year, period))
        return TimeExpression(type='year', year=year, label='%d年' % year)

    # ---- 8. "最新" ----
    if '最新' in text or '最近' in text:
        return TimeExpression(type='latest', label='最新数据')

    # ---- 9. 无时间 ----
    return TimeExpression(type='latest', label='未指定时间')


def _extract_period_suffix(text: str, pos: int) -> Optional[str]:
    """检查 pos 之后是否紧跟报告期关键词，返回 period code"""
    tail = text[pos:pos + 6]  # 最长如 "三季报"
    for kw, code in PERIOD_KEYWORDS.items():
        if tail.startswith(kw):
            return code
    # 也检查 H1/Q1 等英文
    m = re.match(r'[QqHhFf][1-4Yy]', tail)
    if m:
        raw = m.group(0).upper()
        return {'Q1': 'Q1', 'Q2': 'Q2', 'Q3': 'Q3', 'Q4': 'FY',
                'H1': 'H1', 'H2': 'H2', 'FY': 'FY'}.get(raw)
    return None


# ============================================================
# 公开接口（兼容旧代码）
# ============================================================

def parse_time(question: str) -> dict:
    """主入口，返回 dict（兼容旧 pipeline 接口）"""
    return _parse_time(question).to_dict()


def apply_time_to_sql(time_info: dict):
    """
    根据时间解析结果生成 SQL WHERE 条件
    返回 (where_clause, order_by_clause, limit_clause, label)
    """
    t = time_info.get('type', 'latest')

    if t == 'year' or t == 'exact_year':
        year = time_info.get('year')
        period = time_info.get('period')
        where = 'report_year = %d' % year
        if period:
            where += " AND report_period = '%s'" % period
        return (where, '', '', time_info.get('label', ''))

    if t == 'quarter':
        year = time_info.get('year')
        period = time_info.get('period', 'Q1')
        return (
            "report_year = %d AND report_period = '%s'" % (year, period),
            '', '', time_info.get('label', '')
        )

    if t == 'half':
        year = time_info.get('year')
        period = time_info.get('period', 'H1')
        return (
            "report_year = %d AND report_period = '%s'" % (year, period),
            '', '', time_info.get('label', '')
        )

    if t == 'range':
        return (
            'report_year BETWEEN %d AND %d' % (time_info['start_year'], time_info['end_year']),
            '', '', time_info.get('label', '')
        )

    if t == 'rolling' or t == 'trend':
        ys = time_info.get('year_start', CURRENT_YEAR - 3)
        ye = time_info.get('year_end', CURRENT_YEAR - 1)
        return (
            'report_year BETWEEN %d AND %d' % (ys, ye),
            'ORDER BY report_year', '', time_info.get('label', '')
        )

    if t == 'relative':
        year = time_info.get('year')
        if year:
            return ('report_year = %d' % year, '', '', time_info.get('label', ''))
        return ('', 'ORDER BY report_year DESC', 'LIMIT 1', '最新数据')

    if t == 'latest':
        return ('', 'ORDER BY report_year DESC', 'LIMIT 1', '最新数据')

    # 兼容旧的 type 名
    if t == 'exact_year':
        year = time_info.get('year')
        return ('report_year = %d' % year, '', '', time_info.get('label', ''))

    return ('', '', '', '')


def format_time_label(time_info: dict) -> str:
    """人类可读标签"""
    if time_info.get('type') == 'latest':
        return '(未指定时间，默认显示最新数据)'
    return time_info.get('label', '')


# ============================================================
# 单元测试
# ============================================================

if __name__ == '__main__':
    tests = [
        # 裸年份
        ('金花2023净利润', 'year', 2023, None),
        ('2024毛利率', 'year', 2024, None),
        # 带年字
        ('金花2023年净利润', 'year', 2023, None),
        # 短年份
        ('金花23年净利润', 'year', 2023, None),
        # 季度
        ('2023Q1净利润', 'quarter', 2023, 'Q1'),
        ('2023一季度营收', 'quarter', 2023, 'Q1'),
        ('2023三季报现金流', 'quarter', 2023, 'Q3'),
        ('2024年报净利润', 'quarter', 2024, 'FY'),
        # 半年报
        ('2024H1营收', 'half', 2024, 'H1'),
        ('2024半年报净利润', 'half', 2024, 'H1'),
        ('2024中报毛利率', 'half', 2024, 'H1'),
        # 区间
        ('2022到2024净利润变化', 'range', None, None),
        ('2022-2024毛利率', 'range', None, None),
        # 趋势
        ('近三年净利润趋势', 'rolling', None, None),
        ('近五年营收', 'rolling', None, None),
        # 相对时间
        ('去年净利润', 'relative', 2025, None),
        ('前年营收', 'relative', 2024, None),
        # 最新
        ('最新毛利率', 'latest', None, None),
        # 无时间
        ('中药行业分析', 'latest', None, None),
        # 股票代码不应被误识别
        ('金花股份600080', 'latest', None, None),
    ]

    print('时间归一化测试 (%d cases)' % len(tests))
    print('=' * 60)
    passed = 0
    for text, expected_type, expected_year, expected_period in tests:
        result = _parse_time(text, current_year=2026)
        ok_type = result.type == expected_type
        ok_year = (expected_year is None) or (result.year == expected_year)
        ok_period = (expected_period is None) or (result.period == expected_period)
        ok = ok_type and ok_year and ok_period
        status = 'OK' if ok else 'FAIL'
        if ok:
            passed += 1
        print(f'  {status}: "{text}" -> type={result.type}, year={result.year}, period={result.period}, label={result.label}')
        if not ok:
            print(f'        expected: type={expected_type}, year={expected_year}, period={expected_period}')

    print('\n%d/%d passed' % (passed, len(tests)))
