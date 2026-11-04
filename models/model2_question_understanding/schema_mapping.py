# -*- coding: utf-8 -*-
"""
字段映射模块 - 基于同义词典，完全替换 Model1

来源: field_dict.py (57个数据库真实字段同义映射)
       + field_matcher.py (三层匹配逻辑)
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from field_matcher import match as dict_match
from field_matcher import match_from_question as dict_match_from_question
from field_matcher import has_concept_group, get_concept_group


def match(text, top_k=1):
    """
    匹配用户输入的指标词 → 返回字段信息
    输出格式同 Model1 兼容: [{'column_en', 'table_name', 'display', 'score', 'unit'}]
    （不再返回 table_name_cn / column_cn / description）
    """
    return dict_match(text, top_k)


def match_from_question(question):
    """从问题中提取所有匹配字段和概念组"""
    if not question:
        return None
    
    result = {}
    
    # 1. 概念组优先匹配（如 "盈利能力对比" → 命中 "盈利能力"）
    for concept_name in get_all_concepts():
        if concept_name in question:
            fields = get_concept_group(concept_name)
            result[concept_name + '(组合)'] = {
                'column_en': fields[0] if fields else '',
                'table_name': '',
                'unit': '',
                'score': 0.95,
                'display': concept_name + ' -> ' + ', '.join(fields),
                'concept_group': fields
            }
    
    # 2. 同义词匹配
    dict_result = dict_match_from_question(question)
    if dict_result:
        result.update(dict_result)
    
    return result if result else None


def get_all_concepts():
    """获取所有概念组名称"""
    try:
        from field_dict import CONCEPT_GROUPS
        return list(CONCEPT_GROUPS.keys())
    except ImportError:
        return []
