# -*- coding: utf-8 -*-
"""field_mapper.py: 多维相似度语义归并 + 同义词池动态学习"""
import json, os, re
from difflib import SequenceMatcher as SM
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STANDARD = {}
sp = os.path.join(BASE, "data", "schema_columns.json")
if os.path.exists(sp):
    s = json.load(open(sp, encoding="utf-8"))
    for tn, ti in s.get("tables",{}).items():
        for c in ti.get("columns",[]):
            e = {"f": c["en"], "t": tn}
            STANDARD[c["cn"]] = e
            for a in c.get("aliases",[]): STANDARD[a] = e
EXTRA = {
    "营业收入":{"f":"total_operating_revenue","t":"income_sheet"},
    "营业总收入":{"f":"total_operating_revenue","t":"income_sheet"},
    "营收":{"f":"total_operating_revenue","t":"income_sheet"},
    "归属于上市公司股东的净利润":{"f":"net_profit","t":"income_sheet"},
    "归属于母公司股东的净利润":{"f":"net_profit","t":"income_sheet"},
    "归母净利润":{"f":"net_profit","t":"income_sheet"},
    "净利润":{"f":"net_profit","t":"income_sheet"},
    "扣非净利润":{"f":"net_profit_excl_non_recurring","t":"core_performance_indicators_sheet"},
    "总资产":{"f":"asset_total_assets","t":"balance_sheet"},
    "资产总计":{"f":"asset_total_assets","t":"balance_sheet"},
    "归属于上市公司股东的净资产":{"f":"equity_total_equity","t":"balance_sheet"},
    "净资产":{"f":"equity_total_equity","t":"balance_sheet"},
    "所有者权益合计":{"f":"equity_total_equity","t":"balance_sheet"},
    "负债合计":{"f":"liability_total_liabilities","t":"balance_sheet"},
    "总负债":{"f":"liability_total_liabilities","t":"balance_sheet"},
    "经营活动产生的现金流量净额":{"f":"operating_cf_net_amount","t":"cash_flow_sheet"},
    "经营现金流":{"f":"operating_cf_net_amount","t":"cash_flow_sheet"},
    "投资现金流":{"f":"investing_cf_net_amount","t":"cash_flow_sheet"},
    "筹资现金流":{"f":"financing_cf_net_amount","t":"cash_flow_sheet"},
    "基本每股收益":{"f":"eps","t":"core_performance_indicators_sheet"},
    "稀释每股收益":{"f":"eps","t":"core_performance_indicators_sheet"},
    "加权平均净资产收益率":{"f":"roe_weighted_excl_non_recurring","t":"core_performance_indicators_sheet"},
    "ROE":{"f":"roe_weighted_excl_non_recurring","t":"core_performance_indicators_sheet"},
    "营业成本":{"f":"operating_expense_cost_of_sales","t":"income_sheet"},
    "销售费用":{"f":"operating_expense_selling_expenses","t":"income_sheet"},
    "管理费用":{"f":"operating_expense_administrative_expenses","t":"income_sheet"},
    "财务费用":{"f":"operating_expense_financial_expenses","t":"income_sheet"},
    "研发费用":{"f":"operating_expense_rnd_expenses","t":"income_sheet"},
    "货币资金":{"f":"asset_cash_and_cash_equivalents","t":"balance_sheet"},
    "应收账款":{"f":"asset_accounts_receivable","t":"balance_sheet"},
    "存货":{"f":"asset_inventory","t":"balance_sheet"},
    "短期借款":{"f":"liability_short_term_loans","t":"balance_sheet"},
    "应付账款":{"f":"liability_accounts_payable","t":"balance_sheet"},
    "未分配利润":{"f":"equity_unappropriated_profit","t":"balance_sheet"},
    "利润总额":{"f":"total_profit","t":"income_sheet"},
    "每股净资产":{"f":"net_asset_per_share","t":"core_performance_indicators_sheet"},
    "经营活动产生的现金流量净额":{"f":"operating_cf_net_amount","t":"cash_flow_sheet"},
    "投资活动产生的现金流量净额":{"f":"investing_cf_net_amount","t":"cash_flow_sheet"},
    "筹资活动产生的现金流量净额":{"f":"financing_cf_net_amount","t":"cash_flow_sheet"},
    "归属于上市公司股东的扣除非经常性损益的净利润":{"f":"net_profit_excl_non_recurring","t":"core_performance_indicators_sheet"},
    "归属于母公司股东的扣除非经常性损益的净利润":{"f":"net_profit_excl_non_recurring","t":"core_performance_indicators_sheet"},

}

EXCLUSIONS = {
    '经营活动现金流入小计': None,
    '经营活动现金流出小计': None,
    '投资活动现金流入小计': None,
    '投资活动现金流出小计': None,
    '筹资活动现金流入小计': None,
    '筹资活动现金流出小计': None,
    '现金流入小计': None,
    '现金流出小计': None,
}

STANDARD.update(EXTRA)

# ── 同义词池 (动态学习) ─────────────────────────────────
SYNONYM_POOL_PATH = os.path.join(BASE, "data", "synonym_pool.json")
_synonym_pool_loaded = False
_synonym_pool = {}  # {标准字段: [同义词列表]}

def _load_synonym_pool():
    global _synonym_pool, _synonym_pool_loaded
    if _synonym_pool_loaded: return
    _synonym_pool_loaded = True
    if os.path.exists(SYNONYM_POOL_PATH):
        try:
            with open(SYNONYM_POOL_PATH, "r", encoding="utf-8") as f:
                _synonym_pool = json.load(f)
        except:
            _synonym_pool = {}

def _save_synonym_pool():
    try:
        with open(SYNONYM_POOL_PATH, "w", encoding="utf-8") as f:
            json.dump(_synonym_pool, f, ensure_ascii=False, indent=2)
    except:
        pass

def add_to_synonym_pool(std_field, synonym):
    """添加同义词到池中"""
    _load_synonym_pool()
    if std_field not in _synonym_pool:
        _synonym_pool[std_field] = []
    if synonym not in _synonym_pool[std_field]:
        _synonym_pool[std_field].append(synonym)
        _save_synonym_pool()

def get_synonym_score(label, std_field):
    """从同义词池获取匹配分数 (0-25)"""
    _load_synonym_pool()
    synonyms = _synonym_pool.get(std_field, [])
    for syn in synonyms:
        if syn in label or label in syn:
            return 25
    return 0

# ── M3: 频率学习 ────────────────────────────────────────
HISTORY_PATH = os.path.join(BASE, "data", "mapping_history.json")
_history_loaded = False
_mapping_history = {}  # {label: {std_field: {"count":N, "success":N, "total_conf":F}}}

def _load_history():
    global _mapping_history, _history_loaded
    if _history_loaded: return
    _history_loaded = True
    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            _mapping_history = data.get("observations", {})
        except:
            _mapping_history = {}

def _save_history():
    try:
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump({"observations": _mapping_history}, f, ensure_ascii=False, indent=2)
    except:
        pass

def get_freq_score(label, std_key):
    """根据历史映射频率返回 0-25 的评分，考虑count权重"""
    _load_history()
    obs = _mapping_history.get(label, {}).get(std_key)
    if not obs:
        # 初现映射：给基础分5
        return 5
    count = obs.get("count", 0)
    success_rate = obs.get("success", 0) / max(count, 1)
    if count < 3:
        # 样本不足，保守评分
        return 5 + int(success_rate * 10)
    # 样本充足：历史成功率越高分越高，count越高有加成
    base_score = int(success_rate * 20)
    # count加成：count越高，分数越高（最高+5）
    count_bonus = min(5, count // 100)
    return max(0, min(25, base_score + count_bonus))

def learn(label, std_key, matched_conf):
    """记录一次映射观察，高置信度映射加入同义词池"""
    _load_history()
    if label not in _mapping_history:
        _mapping_history[label] = {}
    if std_key not in _mapping_history[label]:
        _mapping_history[label][std_key] = {"count": 0, "success": 0, "total_conf": 0.0}
    obs = _mapping_history[label][std_key]
    obs["count"] += 1
    obs["total_conf"] += matched_conf
    # 高置信度(>=70)视为成功
    if matched_conf >= 70:
        obs["success"] += 1
    # 高置信度(>=85)且样本充足(count>=10)且成功率>=80%才加入同义词池
    if matched_conf >= 85 and obs["count"] >= 10 and obs["success"] / max(obs["count"], 1) >= 0.8:
        add_to_synonym_pool(std_key, label)
    # 每学50次保存一次
    if obs["count"] % 50 == 0:
        _save_history()


def char_overlap(a, b):
    if not a or not b: return 0
    return sum(1 for c in a if c in b) / max(len(a), len(b)) * 100
def edit_sim(a, b):
    return SM(None, a, b).ratio() * 100

def strip_unit_suffix(label):
    """??"????（?????）"??"（?????）"??
    ?? (?????, ????)
    ????（）???()??
    """
    m = re.search(r'[（(][^）)]*[）)]$', label)
    if m:
        suffix = m.group(0)
        core = label[:m.start()]
        if len(core) >= 2:
            return core.strip(), suffix.strip()
    return label, None


def match(label, context_tbl=None):
    if label in EXCLUSIONS:
        return None, 0
    label = label.strip().replace(" ","").replace("　","")
    if not label or len(label) < 2: return None, 0

    # ??????????????
    clean_label, unit_suffix = strip_unit_suffix(label)

    best, best_score, best_std, best_used_clean = None, 0, None, False
    candidates = [(label, False), (clean_label, True)] if clean_label != label else [(label, False)]

    for attempt_label, is_clean in candidates:
        for std, entry in STANDARD.items():
            if not std: continue

            lit = max(char_overlap(attempt_label, std), edit_sim(attempt_label, std)) * 0.3
            if attempt_label == std:
                score = 100 if is_clean else 100
                if score > best_score or (score == best_score and len(std) > len(best_std or "")):
                    best, best_score, best_std, best_used_clean = entry, score, std, is_clean
                continue

            # ????
            if std in attempt_label or attempt_label in std:
                lit = max(lit, 25)

            # 同义词匹配 (从同义词池动态学习，替代硬编码)
            syn = get_synonym_score(attempt_label, std)

            # 前缀模糊匹配：去掉常见前缀后再匹配
            prefix_bonus = 0
            if syn == 0:  # 同义词池没命中才做前缀匹配
                common_prefixes = ["项目", "期末", "期初", "本年", "上年", "本期", "上期"]
                for prefix in common_prefixes:
                    if attempt_label.startswith(prefix):
                        stripped = attempt_label[len(prefix):]
                        if len(stripped) >= 2:
                            # 去掉前缀后和标准字段比较
                            stripped_lit = max(char_overlap(stripped, std), edit_sim(stripped, std)) * 0.3
                            if std in stripped or stripped in std:
                                stripped_lit = max(stripped_lit, 25)
                            if stripped_lit > 20:
                                prefix_bonus = max(prefix_bonus, 15)
                            break

            # 上下文表匹配 (更平衡的奖励)
            if context_tbl and entry["t"] == context_tbl:
                ctx = 15  # 同表加分降低，避免过度偏向
            elif context_tbl and entry["t"] != context_tbl:
                ctx = -10  # 跨表惩罚降低，避免过度排斥
            else:
                ctx = 5

            # ???????????
            # 频率学习分数（M3）: 优先从历史获取，兜底硬编码
            freq_learned = get_freq_score(attempt_label, std)
            freq_hardcoded = 20 if any(kw in std for kw in ["营业收入","净利润","总资产","净资产"]) else 10
            freq = max(freq_learned, freq_hardcoded) if is_clean else freq_hardcoded

            # ????????????????????
            clean_bonus = 10 if is_clean else 0

            total = lit + syn + ctx + freq + clean_bonus + prefix_bonus
            if total > best_score or (total == best_score and len(std) > len(best_std or "")):
                best, best_score, best_std, best_used_clean = entry, total, std, is_clean

    # 严格准入：子串包含关系用40阈值，否则提高到55
    if best:
        is_substring = (best_std in label or label in best_std) if best_std else False
        min_threshold = 40 if is_substring else 55
        if best_score >= min_threshold:
            return best, round(best_score, 1)
    return None, 0
