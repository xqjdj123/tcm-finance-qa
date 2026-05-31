# -*- coding: utf-8 -*-
"""
时间解析器 - 统一处理用户问题中的时间表达

三层逻辑：
1. 正则提取精确年份
2. 语义映射表处理相对时间
3. 都匹配不上 → 默认最新年份

输出格式：
{
    "type": "exact_year" | "year_range" | "latest" | "trend",
    "year": 2024,
    "year_start": 2020,
    "year_end": 2024,
    "period": None | "FY" | "Q1" | "HY" | "Q3",
    "label": "2024年"
}
"""

import re
from datetime import datetime

# ===== 第二层：语义映射表 =====
RELATIVE_TIME_MAP = [
    # (正则, 解析函数)
    # 每条解析函数接收 current_year, 返回 dict
]


def get_current_year():
    return datetime.now().year


def parse_time(question):
    """
    主入口：解析问题中的时间表达
    """
    if not question:
        return {'type': 'latest', 'label': '未指定时间'}
    
    current_year = get_current_year()
    result = _parse_time(question, current_year)
    return result


def _parse_time(question, current_year):
    """内部实现，可单元测试"""
    
    # ========== 第一层：正则提取精确年份 ==========
    
    # 年份范围: "2022年到2024年", "2022-2024", "2022~2024"
    m = re.search(r'(20\d{2})\u5e74(?:到|-|~|∼|—)(20\d{2})\u5e74', question)
    if m:
        ys, ye = int(m.group(1)), int(m.group(2))
        return {
            'type': 'year_range',
            'year_start': min(ys, ye),
            'year_end': max(ys, ye),
            'period': None,
            'label': '%d年-%d年' % (ys, ye)
        }
    
    # 单一年份："2024年"
    m = re.search(r'(20\d{2})\u5e74', question)
    if m:
        year = int(m.group(1))
        return {
            'type': 'exact_year',
            'year': year,
            'period': None,
            'label': '%d年' % year
        }

    # ========== 第二层：语义映射表 ==========
    
    # "今年"
    if '今年' in question:
        return {
            'type': 'exact_year',
            'year': current_year,
            'period': None,
            'label': '今年'
        }
    
    # "去年"
    if '去年' in question:
        return {
            'type': 'exact_year',
            'year': current_year - 1,
            'period': None,
            'label': '去年'
        }
    
    # "前年"
    if '前年' in question:
        return {
            'type': 'exact_year',
            'year': current_year - 2,
            'period': None,
            'label': '前年'
        }
    
    # "近三年"
    m = re.search(r'近(三|五|七|十)\年', question)
    if m:
        num_map = {'三': 3, '五': 5, '七': 7, '十': 10}
        n = num_map[m.group(1)]
        return {
            'type': 'trend',
            'year_start': current_year - n,
            'year_end': current_year - 1,
            'period': None,
            'label': '近%d年' % n
        }
    
    # "近几年" - 约定5年
    if '近几年' in question or '最近几年' in question:
        return {
            'type': 'trend',
            'year_start': current_year - 5,
            'year_end': current_year - 1,
            'period': None,
            'label': '近几年'
        }
    
    # "最近五年" / "最近5年"
    m = re.search(r'最近(\d+)\年', question)
    if m:
        n = int(m.group(1))
        return {
            'type': 'trend',
            'year_start': current_year - n,
            'year_end': current_year - 1,
            'period': None,
            'label': '最近%d年' % n
        }
    
    # "前三年" - 过去三年（不含当年）
    if '前三年' in question:
        return {
            'type': 'trend',
            'year_start': current_year - 3,
            'year_end': current_year - 1,
            'period': None,
            'label': '前三年'
        }
    
    # "近N个季度" - 最近N个报告期
    # 处理"两个""三个"等汉字数字
    _cn_num = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    m = re.search(r'近([一-鿿\d+])\个季度', question)
    if m:
        raw = m.group(1)
        n = _cn_num.get(raw, int(raw) if raw.isdigit() else 2)
        return {
            'type': 'latest_quarters',
            'count': n,
            'label': '近%d个季度' % n
        }
    if m:
        n = int(m.group(1))
        return {
            'type': 'latest_quarters',
            'count': n,
            'label': '近%d个季度' % n
        }
    
    # ========== 第三层：默认最新年份 ==========
    return {
        'type': 'latest',
        'label': '未指定时间'
    }


def apply_time_to_sql(time_info):
    """
    根据时间解析结果生成 SQL WHERE 条件
    返回 (where_clause, order_by_clause, limit_clause, label)
    """
    t = time_info['type']
    
    if t == 'exact_year':
        return (
            'report_year = ' + str(time_info['year']),
            '',
            '',
            time_info['label']
        )
    
    elif t == 'year_range':
        return (
            'report_year BETWEEN ' + str(time_info['year_start']) + ' AND ' + str(time_info['year_end']),
            '',
            '',
            time_info['label']
        )
    
    elif t == 'trend':
        return (
            'report_year BETWEEN ' + str(time_info['year_start']) + ' AND ' + str(time_info['year_end']),
            'ORDER BY report_year',
            '',
            time_info['label']
        )
    
    elif t == 'latest':
        return (
            '',
            'ORDER BY report_year DESC',
            'LIMIT 1',
            '最新数据'
        )
    
    elif t == 'latest_quarters':
        return (
            '',
            'ORDER BY report_year DESC, report_period DESC',
            'LIMIT ' + str(time_info.get('count', 2)),
            time_info['label']
        )
    
    return ('', '', '', '')


def format_time_label(time_info):
    """精美时间标签（用于答案展示）"""
    if time_info['type'] == 'latest':
        return '(未指定时间，默认显示最新数据)'
    return time_info['label']
