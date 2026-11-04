# -*- coding: utf-8 -*-
"""
字段匹配引擎 - 替换 Model1
三层逻辑：
1. 精确匹配同义词表 → 返回真实列名+表名+单位
2. 模糊匹配（子串/Jaccard）→ 返回最佳候选
3. 无匹配 → 返回 None
"""

import re
from field_dict import SCHEMA_DICT, CONCEPT_GROUPS


def _build_synonym_index():
    """构建同义词→字段名倒排索引"""
    index = {}
    for field_name, info in SCHEMA_DICT.items():
        for syn in info['synonyms']:
            index[syn] = field_name
    return index


SYNONYM_INDEX = _build_synonym_index()


def match(text, top_k=1):
    """
    主匹配函数，返回格式兼容 Model1 的 match() 输出
    输入: 用户说的指标词（如 "净利润"）
    输出: [{'column_en': 'net_profit', 'table_name': 'income_sheet', 'unit': 'wan', 'score': 1.0, ...}]
    """
    if not text or not text.strip():
        return None

    text = text.strip()
    results = []

    # ========== 第一层: 精确同义匹配 ==========
    # 优先匹配完整同义词
    matched = SYNONYM_INDEX.get(text)
    if matched:
        info = SCHEMA_DICT[matched]
        results.append({
            'column_en': matched,
            'table_name': info['table'],
            'unit': info['unit'],
            'match_type': 'exact',
            'score': 1.0,
            'display': info['table'] + '.' + matched + ': ' + info['label'],
        })
        return results[:top_k]

    # ========== 第二层: 概念组合匹配 ==========
    # 检查是否匹配概念组
    if text in CONCEPT_GROUPS:
        group_fields = CONCEPT_GROUPS[text]
        for fn in group_fields:
            info = SCHEMA_DICT[fn]
            results.append({
                'column_en': fn,
                'table_name': info['table'],
                'unit': info['unit'],
                'match_type': 'concept_group',
                'score': 0.95,
                'display': info['table'] + '.' + fn + ': ' + info['label'],
                'concept_group': text,
            })
        return results[:top_k] if top_k else results

    # ========== 第三层: 子串匹配 ==========
    # 同义词包含用户输入（如用户说"毛利"，同义词有"毛利率"）
    substring_matches = []
    for syn, field_name in SYNONYM_INDEX.items():
        if text in syn:
            score = len(text) / max(len(syn), 1) * 0.9
            substring_matches.append((score, field_name, syn))

    if substring_matches:
        substring_matches.sort(key=lambda x: -x[0])
        for score, field_name, syn in substring_matches[:top_k]:
            info = SCHEMA_DICT[field_name]
            results.append({
                'column_en': field_name,
                'table_name': info['table'],
                'unit': info['unit'],
                'match_type': 'substring',
                'score': round(score, 4),
                'display': info['table'] + '.' + field_name + ': ' + syn,
                'matched_synonym': syn,
            })
        results.sort(key=lambda x: -x['score'])
        return results[:top_k]

    # ========== 无匹配 ==========
    return None


def match_from_question(question):
    """从问题中提取所有匹配字段（去重：长词覆盖短词）"""
    if not question:
        return None
    
    # 先按长度降序排列的所有同义词
    all_syns = sorted(SYNONYM_INDEX.items(), key=lambda x: -len(x[0]))
    raw_matches = []
    
    for syn, field_name in all_syns:
        if syn in question:
            # 检查这个同义词是否被更长的同义词覆盖
            is_covered = False
            for other_syn, other_field in raw_matches:
                if other_syn != syn and syn in other_syn:
                    # 更长的同义词已经匹配，跳过这个短的
                    is_covered = True
                    break
            if not is_covered:
                raw_matches.append((syn, field_name))
    
    result = {}
    for syn, field_name in raw_matches:
        info = SCHEMA_DICT[field_name]
        result[syn] = {
            'column_en': field_name,
            'table_name': info['table'],
            'unit': info['unit'],
            'score': round(len(syn) / max(len(question), 1) * 0.8 + 0.2, 4),
            'display': info['table'] + '.' + field_name + ': ' + syn,
        }
    return result if result else None


def has_concept_group(text):
    """检查是否匹配概念组"""
    return text in CONCEPT_GROUPS


def get_concept_group(name):
    """获取概念组包含的字段列表"""
    return CONCEPT_GROUPS.get(name, [])
