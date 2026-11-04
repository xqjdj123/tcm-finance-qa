# -*- coding: utf-8 -*-
"""T6 (S2) 字段知识收敛 — taxonomy 与 field_dict 一致性回归单测。

收敛方向：models/model2_question_understanding/field_dict.py 的
SCHEMA_DICT + CONCEPT_GROUPS 是字段(label/synonyms/table/基础概念组)的
单一真相源；agent/taxonomy.py 只在其上补充 FieldCategory 分类/单位与概念组
别名/描述。任何一边漂移（taxonomy 长出 field_dict 没有的真实字段、label/
同义词被再次复制改口、分类推导与 unit 语义脱节）都会使本文件失败。

运行（目标解释器）：
    python -m pytest tests/test_agent_phase1_s2.py -q
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 先导入 agent.taxonomy 使其把 models/model2_question_understanding 注入
# sys.path 并注册真相源，再以同名模块导入 field_dict —— 与 taxonomy 共享
# 同一个 SCHEMA_DICT/CONCEPT_GROUPS 实例，防止出现两份漂移副本。
import agent.taxonomy as taxonomy
from field_dict import SCHEMA_DICT, CONCEPT_GROUPS


_GROWTH_RE = re.compile(r"(_yoy|_qoq|_growth)")


class TestFieldRegistryAligned(unittest.TestCase):
    """字段注册表与真相源逐字段对齐（防漂移核心）。"""

    def test_field_key_sets_equal(self):
        """taxonomy 不得有 field_dict 没有的真实字段，也不得缺字段。"""
        tx_keys = set(taxonomy.INDICATORS)
        fd_keys = set(SCHEMA_DICT)
        self.assertEqual(sorted(tx_keys - fd_keys), [])
        self.assertEqual(sorted(fd_keys - tx_keys), [])
        self.assertEqual(len(taxonomy.INDICATORS), len(SCHEMA_DICT))

    def test_label_table_synonyms_aligned(self):
        """label/table/synonyms 必须与真相源逐字段一致（顺序也一致）。"""
        for en_name, info in SCHEMA_DICT.items():
            meta = taxonomy.INDICATORS[en_name]
            self.assertEqual(meta.cn_label, info["label"], en_name)
            self.assertEqual(meta.table, info["table"], en_name)
            self.assertEqual(meta.synonyms, info["synonyms"], en_name)

    def test_no_duplicate_cn_labels(self):
        """中文标签唯一：en_to_cn/build_en_cn_map 等映射保持确定性。"""
        labels = [m.cn_label for m in taxonomy.INDICATORS.values()]
        self.assertEqual(len(labels), len(set(labels)))
        en_map = taxonomy.build_en_cn_map()
        cn_map = taxonomy.build_cn_en_map_for_llm()
        self.assertEqual(len(en_map), len(SCHEMA_DICT))
        self.assertEqual(len(cn_map), len(SCHEMA_DICT))


class TestCategorySupplement(unittest.TestCase):
    """分类补充与 field_dict.unit 语义的一致性（taxonomy 独有补充部分）。"""

    _EXPECTED_UNITS = {
        taxonomy.FieldCategory.AMOUNT: ("万元", "亿元"),
        taxonomy.FieldCategory.RATIO: ("1", "%"),
        taxonomy.FieldCategory.GROWTH: ("1", "%"),
        taxonomy.FieldCategory.PER_SHARE: ("元", "元/股"),
        taxonomy.FieldCategory.COUNT: ("股", "股"),
    }

    def test_category_derivable_from_unit_and_name(self):
        """每个字段的 category 都能由 unit+字段名推导，说明无第二份分类复制。"""
        for en_name, info in SCHEMA_DICT.items():
            meta = taxonomy.INDICATORS[en_name]
            unit = info.get("unit", "")
            if unit == "yuan_per_share":
                expected = taxonomy.FieldCategory.PER_SHARE
            elif unit == "ratio":
                expected = (taxonomy.FieldCategory.GROWTH if _GROWTH_RE.search(en_name)
                            else taxonomy.FieldCategory.RATIO)
            elif en_name == "total_share_capital":
                # 总股本=股数：unit='wan' 口径表达不了，taxonomy 显式覆盖为 COUNT
                expected = taxonomy.FieldCategory.COUNT
            else:
                expected = taxonomy.FieldCategory.AMOUNT
            self.assertEqual(meta.category, expected, en_name)

    def test_source_display_unit_match_category(self):
        """单位补充必须与 category 配套（converter 依赖该契约）。"""
        for en_name, meta in taxonomy.INDICATORS.items():
            expected = self._EXPECTED_UNITS[meta.category]
            self.assertEqual((meta.source_unit, meta.display_unit), expected, en_name)

    def test_b3_cn_mapping_chain_survives(self):
        """b3 依赖的中文映射链：net_profit -> 净利润 必须可用。"""
        self.assertEqual(taxonomy.en_to_cn("net_profit"), "净利润")
        self.assertEqual(taxonomy.cn_to_en("净利润"), "net_profit")
        found = taxonomy.find_indicator("税后利润")
        self.assertIsNotNone(found)
        self.assertEqual(found.en_name, "net_profit")


class TestConceptGroupsAligned(unittest.TestCase):
    """概念组：taxonomy 基础成员继承真相源，只增不减。"""

    def test_all_field_dict_groups_present_and_superset(self):
        self.assertTrue(set(CONCEPT_GROUPS) <= set(taxonomy.CONCEPT_GROUPS))
        for name, members in CONCEPT_GROUPS.items():
            self.assertIn(name, taxonomy.CONCEPT_GROUPS)
            tx_members = taxonomy.CONCEPT_GROUPS[name]["indicators"]
            for member in members:
                self.assertIn(member, tx_members, name)

    def test_group_members_are_real_fields_and_metadata_kept(self):
        for name, group in taxonomy.CONCEPT_GROUPS.items():
            self.assertIn("indicators", group)
            self.assertIn("aliases", group)
            self.assertIn("description", group)
            for member in group["indicators"]:
                self.assertIn(member, SCHEMA_DICT, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
